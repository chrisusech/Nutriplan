"""Planes: listado, revisión y aprobación (pantalla 1d), export y edición."""

from datetime import datetime
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.approve_plan import approve_plan
from nutriplan.application.edit_plan import (
    persist_day_edit,
    remove_food_from_slot,
    resolve_day,
    swap_food_in_slot,
    validate_swap_food,
)
from nutriplan.domain.errors import GenerationError, QuotaExceededError
from nutriplan.domain.models import (
    DayPlan,
    MealEntry,
    MealItem,
    MealSlot,
    PlanCycle,
    PlanStatus,
    Role,
)
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import (
    acting_trainer,
    container_of,
    db_session,
    render,
    repos_of,
)
from nutriplan.ui.web.routes.generator import _generator_context

router = APIRouter()

def _find_day(cycle: PlanCycle, day_index: int) -> DayPlan | None:
    return presenter.get_plan_day(cycle, day_index)


def _find_meal(day: DayPlan, slot_value: str) -> MealEntry | None:
    try:
        slot = MealSlot(slot_value)
    except ValueError:
        return None
    return next((m for m in day.meals if m.slot is slot), None)


async def _edit_response(
    request: Request,
    session: AsyncSession,
    *,
    cycle: PlanCycle,
    client_id: UUID,
    day_index: int,
) -> Response:
    client = await repos_of(request, session).clients.get(client_id)
    if client is None:
        return RedirectResponse("/generador", status_code=303)
    ctx = await _generator_context(
        request, session, client, day_index, editar=True
    )
    return render(request, "partials/generator_body.html", active_tab="generador", ctx=ctx)


@router.get("/planes", response_class=HTMLResponse)
async def plans_index(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)]) -> HTMLResponse:
    repos = repos_of(request, session)
    rows = []
    for client in await repos.clients.list_all():
        cycles = await repos.plans.list_for_client(client.id)
        # El plan ACTIVO, no "el último": es el que el cliente está siguiendo.
        plan = presenter.active_plan(client, cycles)
        if plan is None:
            continue
        targets = await repos.targets.get(plan.targets_id)
        card = presenter.client_card(client, cycles, targets)
        rows.append({**card, "cycle_id": str(plan.id),
                     "client_id": str(client.id),
                     "version": plan.version,
                     "n_versions": len(cycles),
                     "created": presenter.time_ago(plan.created_at)})
    return render(request, "plans.html", active_tab="planes", rows=rows)


@router.get("/planes/cliente/{client_id}", response_model=None)
async def plan_history(request: Request,
                       session: Annotated[AsyncSession, Depends(db_session)],
                       client_id: str) -> Response:
    """El historial de versiones de un cliente: la puerta que no existía.

    Los planes viejos siempre estuvieron en la base, con su fecha y sus macros; lo
    que no había era forma de llegar a ellos — `/planes` solo pintaba el más nuevo y
    el resto quedaban huérfanos. `/planes/{id}` ya sabía renderizar cualquier ciclo:
    no faltaba capacidad, faltaba un enlace.
    """
    repos = repos_of(request, session)
    client = await repos.clients.get(UUID(client_id))
    if client is None:
        return RedirectResponse("/planes", status_code=303)

    cycles = await repos.plans.list_for_client(client.id)
    rows = []
    # Puntos de la gráfica: solo versiones aprobadas (las definitivas), con peso.
    progress: list[tuple[datetime, float, int]] = []
    for cycle in cycles:  # DESC: la versión más nueva primero
        targets = await repos.targets.get(cycle.targets_id)
        if (
            cycle.status == PlanStatus.APPROVED
            and targets is not None
            and targets.weight_kg is not None
        ):
            progress.append((cycle.created_at, targets.weight_kg, cycle.version))
        rows.append({
            "id": str(cycle.id),
            "version": cycle.version,
            "active": cycle.id == client.active_plan_id,
            "created": cycle.created_at.strftime("%d %b %Y"),
            "ago": presenter.time_ago(cycle.created_at),
            "status": cycle.status.value,
            "approved": cycle.status == PlanStatus.APPROVED,
            # El peso y los macros DE ENTONCES, no los de hoy: es el seguimiento.
            "weight": f"{targets.weight_kg:g}" if targets and targets.weight_kg else "—",
            "kcal": presenter.fmt_kcal(targets.daily.kcal) if targets else "—",
            "macros": (
                f"P {round(targets.daily.protein_g)} · C {round(targets.daily.carb_g)} · "
                f"G {round(targets.daily.fat_g)}"
                if targets else "—"
            ),
        })
    # Cronológico (los cycles vienen DESC): la gráfica va del más viejo al más nuevo.
    chart = presenter.weight_progress_chart(list(reversed(progress)))
    return render(request, "plan_history.html", active_tab="planes",
                  client=client, rows=rows, chart=chart)


@router.post("/planes/{cycle_id}/activar")
async def activate(request: Request,
                   session: Annotated[AsyncSession, Depends(db_session)],
                   cycle_id: str) -> RedirectResponse:
    """Marca este plan como EL definitivo. Activar uno archiva al anterior."""
    repos = repos_of(request, session)
    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None:
        return RedirectResponse("/planes", status_code=303)
    await repos.clients.set_active_plan(cycle.client_id, cycle.id)
    return RedirectResponse(f"/planes/cliente/{cycle.client_id}", status_code=303)


@router.get("/planes/{cycle_id}", response_model=None)
async def review_plan(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)],
                      cycle_id: str) -> Response:
    container = container_of(request)
    repos = repos_of(request, session)
    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None:
        return RedirectResponse("/planes", status_code=303)
    client = await repos.clients.get(cycle.client_id)
    if client is None:
        return RedirectResponse("/planes", status_code=303)

    targets = await repos.targets.get(cycle.targets_id)
    if targets is None:
        targets = await repos.targets.latest_for_client(client.id)
    if targets is None:
        return RedirectResponse("/planes", status_code=303)
    config = container.nutrition_config(client)


    # Los alimentos hacen falta para saber CON QUÉ reparto se porcionó cada día:
    # sin ellos, un día con snack de solo fruta se pintaría como "no cuadra".
    all_ids = {item.food_id for d in cycle.days for m in d.meals
               for item in m.items if item.food_id}
    foods_map = {f.id: f for f in await repos.foods.get_by_ids(sorted(all_ids, key=str))}
    grid = presenter.week_grid(cycle, targets, config, foods=foods_map)
    fit_days = sum(1 for cell in grid if cell["fit"])
    approved = cycle.status == PlanStatus.APPROVED
    goal_meta = presenter.GOAL_META[client.goal]
    return render(
        request, "review.html", active_tab="planes",
        client=client, cycle=cycle, grid=grid, fit_days=fit_days,
        adh=presenter.adherence(cycle, targets, config),
        approved=approved,
        error=request.query_params.get("error"),
        is_active=client.active_plan_id == cycle.id,
        subtitle=(f"{goal_meta['label']} · {presenter.fmt_kcal(targets.daily.kcal)} kcal · "
                  f"generado {presenter.time_ago(cycle.created_at)}"),
    )


@router.post("/planes/{cycle_id}/aprobar")
async def approve(request: Request,
                  session: Annotated[AsyncSession, Depends(db_session)],
                  cycle_id: str) -> RedirectResponse:
    repos = repos_of(request, session)
    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None:
        return RedirectResponse(f"/planes/{cycle_id}", status_code=303)
    # El super_user no tiene cupo; el entrenador aprueba hasta su tope de versiones.
    actor = await acting_trainer(request, session)
    limit = None if (actor is None or actor.role == Role.SUPER_USER) else actor.max_menus
    try:
        await approve_plan(
            plan_id=cycle.id, plan_repo=repos.plans, audit_repo=repos.audit,
            max_menus=limit,
        )
    except QuotaExceededError as exc:
        return RedirectResponse(
            f"/planes/{cycle_id}?error={quote(str(exc))}", status_code=303
        )
    return RedirectResponse(f"/planes/{cycle_id}", status_code=303)


@router.post("/planes/{cycle_id}/dia/{day_index}/porciones", response_model=None)
async def edit_portions(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)],
                        cycle_id: str, day_index: int) -> Response:
    """Edición en línea: nuevos gramos → recálculo → persistir vía update_day."""
    repos = repos_of(request, session)
    form = await request.form()
    slot_value = str(form.get("slot", ""))
    cliente = str(form.get("cliente", ""))

    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None or cycle.status not in (PlanStatus.DRAFT, PlanStatus.APPROVED):
        return RedirectResponse(f"/generador?cliente={cliente}", status_code=303)

    day = _find_day(cycle, day_index)
    meal = _find_meal(day, slot_value) if day else None
    if day is None or meal is None:
        return RedirectResponse(
            f"/generador?cliente={cliente}&editar=1", status_code=303
        )
    edited_slot = meal.slot

    new_items: list[MealItem] = []
    for item in meal.items:
        if item.food_id is None:
            continue
        raw = form.get(f"grams_{item.food_id}")
        grams = item.grams or 0.0
        if raw is not None:
            try:
                grams = max(float(str(raw)), 0.0)
            except ValueError:
                grams = item.grams or 0.0
        if grams > 0:
            new_items.append(
                MealItem(
                    id=item.id,
                    food_id=item.food_id,
                    grams=grams,
                    position=item.position,
                    is_locked=True,
                )
            )
    if not new_items:
        return await _edit_response(
            request, session, cycle=cycle, client_id=cycle.client_id,
            day_index=day_index,
        )

    meal.items = new_items
    container = container_of(request)
    client = await repos.clients.get(cycle.client_id)
    if client is None:
        return RedirectResponse("/planes", status_code=303)
    config = container.nutrition_config(client)
    targets = await repos.targets.latest_for_client(cycle.client_id)
    if targets is not None:
        all_ids = {
            item.food_id for m in day.meals for item in m.items if item.food_id
        }
        foods_map = {
            f.id: f for f in await repos.foods.get_by_ids(sorted(all_ids, key=str))
        }
        try:
            day = await resolve_day(day, foods_map, targets, config, edited_slot=edited_slot)
        except GenerationError:
            return await _edit_response(
                request, session, cycle=cycle, client_id=cycle.client_id,
                day_index=day_index,
            )
        await persist_day_edit(
            plan_repo=repos.plans,
            cycle=cycle,
            day_index=day_index,
            day=day,
        )

    return await _edit_response(
        request, session, cycle=cycle, client_id=cycle.client_id,
        day_index=day_index,
    )


@router.post("/planes/{cycle_id}/dia/{day_index}/cambiar", response_model=None)
async def swap_food(request: Request,
                    session: Annotated[AsyncSession, Depends(db_session)],
                    cycle_id: str, day_index: int) -> Response:
    """Sustituye un alimento en un slot y re-solve solo ese día."""
    repos = repos_of(request, session)
    form = await request.form()
    cliente = str(form.get("cliente", ""))
    slot_value = str(form.get("slot", ""))
    try:
        old_food_id = UUID(str(form.get("old_food_id", "")))
        new_food_id = UUID(str(form.get("new_food_id", "")))
    except ValueError:
        return RedirectResponse(f"/generador?cliente={cliente}&editar=1", status_code=303)

    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None or cycle.status not in (PlanStatus.DRAFT, PlanStatus.APPROVED):
        return RedirectResponse(f"/generador?cliente={cliente}", status_code=303)

    client = await repos.clients.get(cycle.client_id)
    if client is None:
        return RedirectResponse("/generador", status_code=303)

    container = container_of(request)
    config = container.nutrition_config(client)
    targets = await repos.targets.latest_for_client(cycle.client_id)
    if targets is None:
        return RedirectResponse(f"/generador?cliente={cliente}&editar=1", status_code=303)

    new_foods = await repos.foods.get_by_ids([new_food_id])
    if not new_foods:
        return RedirectResponse(
            f"/generador?cliente={cliente}&editar=1", status_code=303
        )
    new_food = new_foods[0]

    try:
        slot = MealSlot(slot_value)
        await validate_swap_food(
            client=client,
            food_repo=repos.foods,
            client_repo=repos.clients,
            new_food=new_food,
            slot=slot,
        )
    except GenerationError:
        return await _edit_response(
            request, session, cycle=cycle, client_id=cycle.client_id,
            day_index=day_index,
        )

    all_ids = {
        item.food_id
        for d in cycle.days
        for m in d.meals
        for item in m.items
        if item.food_id
    }
    all_ids.add(new_food_id)
    foods_map = {f.id: f for f in await repos.foods.get_by_ids(sorted(all_ids, key=str))}

    try:
        day = await swap_food_in_slot(
            cycle=cycle,
            day_index=day_index,
            slot=slot,
            old_food_id=old_food_id,
            new_food=new_food,
            foods=foods_map,
            targets=targets,
            config=config,
        )
        await persist_day_edit(
            plan_repo=repos.plans, cycle=cycle, day_index=day_index, day=day
        )
    except GenerationError:
        pass

    return await _edit_response(
        request, session, cycle=cycle, client_id=cycle.client_id,
        day_index=day_index,
    )


@router.post("/planes/{cycle_id}/dia/{day_index}/quitar", response_model=None)
async def remove_food(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)],
                      cycle_id: str, day_index: int) -> Response:
    """Quita un alimento del slot, re-solve el día y opcionalmente lo banea."""
    from nutriplan.application.edit_plan import ban_for_client

    repos = repos_of(request, session)
    form = await request.form()
    cliente = str(form.get("cliente", ""))
    slot_value = str(form.get("slot", ""))
    try:
        food_id = UUID(str(form.get("food_id", "")))
    except ValueError:
        return RedirectResponse(f"/generador?cliente={cliente}&editar=1", status_code=303)
    do_ban = str(form.get("ban", "")).lower() in ("1", "true", "on")

    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None or cycle.status not in (PlanStatus.DRAFT, PlanStatus.APPROVED):
        return RedirectResponse(f"/generador?cliente={cliente}", status_code=303)

    container = container_of(request)
    client = await repos.clients.get(cycle.client_id)
    if client is None:
        return RedirectResponse("/generador", status_code=303)
    config = container.nutrition_config(client)
    targets = await repos.targets.latest_for_client(cycle.client_id)
    if targets is None:
        return RedirectResponse(f"/generador?cliente={cliente}&editar=1", status_code=303)

    all_ids = {
        item.food_id
        for d in cycle.days
        for m in d.meals
        for item in m.items
        if item.food_id
    }
    foods_map = {f.id: f for f in await repos.foods.get_by_ids(sorted(all_ids, key=str))}

    try:
        day = await remove_food_from_slot(
            cycle=cycle,
            day_index=day_index,
            slot=MealSlot(slot_value),
            food_id=food_id,
            foods=foods_map,
            targets=targets,
            config=config,
        )
        await persist_day_edit(
            plan_repo=repos.plans, cycle=cycle, day_index=day_index, day=day
        )
        if do_ban:
            await ban_for_client(
                client_repo=repos.clients, client_id=cycle.client_id, food_id=food_id
            )
    except (GenerationError, ValueError):
        pass

    return await _edit_response(
        request, session, cycle=cycle, client_id=cycle.client_id,
        day_index=day_index,
    )
