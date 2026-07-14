"""Planes: listado, revisión y aprobación (pantalla 1d), export y edición."""

import unicodedata
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.approve_plan import approve_plan, reopen_plan
from nutriplan.application.edit_plan import (
    persist_day_edit,
    remove_food_from_slot,
    resolve_day,
    swap_food_in_slot,
    validate_swap_food,
)
from nutriplan.application.export_plan import export_plan
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.models import (
    DayPlan,
    MealEntry,
    MealItem,
    MealSlot,
    PlanPhase,
    PlanStatus,
)
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import container_of, db_session, render, repos_of, tenant_of
from nutriplan.ui.web.routes.generator import _generator_context

router = APIRouter()

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _parse_phase(raw: str | None) -> PlanPhase:
    return presenter.parse_plan_phase(raw)


async def _phase_from_request(request: Request, query_fase: str) -> PlanPhase:
    form = await request.form()
    return _parse_phase(str(form.get("fase") or query_fase or ""))


def _find_day(cycle, phase: PlanPhase, day_index: int) -> DayPlan | None:
    return presenter.get_plan_day(cycle, phase, day_index)


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
    cycle,
    client_id: UUID,
    day_index: int,
    phase: PlanPhase,
) -> Response:
    client = await repos_of(request, session).clients.get(client_id)
    if client is None:
        return RedirectResponse("/generador", status_code=303)
    ctx = await _generator_context(
        request, session, client, day_index, editar=True, fase=phase.value
    )
    return render(request, "partials/generator_body.html", active_tab="generador", ctx=ctx)


def _attachment(filename: str) -> str:
    """Content-Disposition seguro para nombres con tilde ("Ana Pérez").

    Los headers HTTP no son UTF-8: se manda un fallback ASCII y el nombre real
    en el parámetro extendido de la RFC 6266, que es lo que lee el navegador.
    """
    ascii_name = (
        unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode() or "plan"
    )
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/planes", response_class=HTMLResponse)
async def plans_index(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)]) -> HTMLResponse:
    repos = repos_of(request, session)
    rows = []
    for client in await repos.clients.list():
        cycles = await repos.plans.list_for_client(client.id)
        plan = presenter.latest_plan(cycles)
        if plan is None:
            continue
        targets = await repos.targets.get(plan.targets_id)
        card = presenter.client_card(client, cycles, targets)
        rows.append({**card, "cycle_id": str(plan.id),
                     "created": presenter.time_ago(plan.created_at)})
    return render(request, "plans.html", active_tab="planes", rows=rows)


@router.get("/planes/{cycle_id}", response_model=None)
async def review_plan(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)],
                      cycle_id: str,
                      fase: str = "first_15") -> Response:
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

    phases = presenter.plan_phases(cycle)
    phase = _parse_phase(fase)
    if phase not in phases:
        phase = phases[0]

    # Los alimentos hacen falta para saber CON QUÉ reparto se porcionó cada día:
    # sin ellos, un día con snack de solo fruta se pintaría como "no cuadra".
    all_ids = {item.food_id for d in cycle.days for m in d.meals
               for item in m.items if item.food_id}
    foods_map = {f.id: f for f in await repos.foods.get_by_ids(sorted(all_ids, key=str))}
    grid = presenter.week_grid(cycle, targets, config, phase, foods=foods_map)
    fit_days = sum(1 for cell in grid if cell["fit"])
    approved = cycle.status == PlanStatus.APPROVED
    goal_meta = presenter.GOAL_META[client.goal]
    phase_label = presenter.PHASE_LABELS.get(phase, "Semana 1")
    span_label = phase_label if len(phases) > 1 else "7 días"
    return render(
        request, "review.html", active_tab="planes",
        client=client, cycle=cycle, grid=grid, fit_days=fit_days,
        adh=presenter.adherence(cycle, targets, phase),
        approved=approved, phases=phases, fase=phase.value,
        subtitle=(f"{goal_meta['label']} · {presenter.fmt_kcal(targets.daily.kcal)} kcal · "
                  f"{presenter.duration_label(cycle)} · generado {presenter.time_ago(cycle.created_at)}"),
    )


@router.post("/planes/{cycle_id}/aprobar")
async def approve(request: Request,
                  session: Annotated[AsyncSession, Depends(db_session)],
                  cycle_id: str) -> RedirectResponse:
    repos = repos_of(request, session)
    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is not None:
        await approve_plan(plan_id=cycle.id, plan_repo=repos.plans, audit_repo=repos.audit)
    return RedirectResponse(f"/planes/{cycle_id}", status_code=303)


@router.post("/planes/{cycle_id}/reabrir")
async def reopen(request: Request,
                 session: Annotated[AsyncSession, Depends(db_session)],
                 cycle_id: str) -> RedirectResponse:
    """Reabre un plan aprobado para corregirlo, y lleva al editor del generador."""
    repos = repos_of(request, session)
    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None:
        return RedirectResponse("/planes", status_code=303)
    await reopen_plan(plan_id=cycle.id, plan_repo=repos.plans, audit_repo=repos.audit)
    return RedirectResponse(f"/generador?cliente={cycle.client_id}&editar=1", status_code=303)


@router.get("/planes/{cycle_id}/export.{fmt}")
async def export(request: Request,
                 session: Annotated[AsyncSession, Depends(db_session)],
                 cycle_id: str, fmt: str) -> Response:
    if fmt not in MEDIA_TYPES:
        return RedirectResponse(f"/planes/{cycle_id}", status_code=303)
    container = container_of(request)
    repos = repos_of(request, session)
    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None or cycle.status != PlanStatus.APPROVED:
        return RedirectResponse(f"/planes/{cycle_id}", status_code=303)
    client = await repos.clients.get(cycle.client_id)
    targets = await repos.targets.get(cycle.targets_id)

    _, content = await export_plan(
        plan_id=cycle.id, fmt=fmt,  # type: ignore[arg-type]
        plan_repo=repos.plans, food_repo=repos.foods, artifact_repo=repos.artifacts,
        renderer=container.renderer_for(fmt), branding=container.branding(tenant_of(request)),
        exports_dir=container.settings.exports_dir,
        client_name=client.name if client else None,
        daily_targets=targets.daily if targets else None,
    )
    who = (client.name if client else "cliente").replace(" ", "_")
    dur = "30d" if cycle.duration_days >= 30 else "15d"
    return Response(
        content=content, media_type=MEDIA_TYPES[fmt],
        headers={"Content-Disposition": _attachment(f"plan_{dur}_{who}.{fmt}")},
    )


@router.post("/planes/{cycle_id}/dia/{day_index}/porciones", response_model=None)
async def edit_portions(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)],
                        cycle_id: str, day_index: int,
                        fase: str = "first_15") -> Response:
    """Edición en línea: nuevos gramos → recálculo → persistir vía update_day."""
    repos = repos_of(request, session)
    form = await request.form()
    slot_value = str(form.get("slot", ""))
    cliente = str(form.get("cliente", ""))
    phase = await _phase_from_request(request, fase)

    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None or cycle.status != PlanStatus.DRAFT:
        return RedirectResponse(f"/generador?cliente={cliente}", status_code=303)

    day = _find_day(cycle, phase, day_index)
    meal = _find_meal(day, slot_value) if day else None
    if day is None or meal is None:
        return RedirectResponse(
            f"/generador?cliente={cliente}&editar=1&fase={phase.value}", status_code=303
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
            day_index=day_index, phase=phase,
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
                day_index=day_index, phase=phase,
            )
        await persist_day_edit(
            plan_repo=repos.plans,
            cycle=cycle,
            phase=phase,
            day_index=day_index,
            day=day,
        )

    return await _edit_response(
        request, session, cycle=cycle, client_id=cycle.client_id,
        day_index=day_index, phase=phase,
    )


@router.post("/planes/{cycle_id}/dia/{day_index}/cambiar", response_model=None)
async def swap_food(request: Request,
                    session: Annotated[AsyncSession, Depends(db_session)],
                    cycle_id: str, day_index: int,
                    fase: str = "first_15") -> Response:
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
    phase = await _phase_from_request(request, fase)

    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None or cycle.status != PlanStatus.DRAFT:
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
            f"/generador?cliente={cliente}&editar=1&fase={phase.value}", status_code=303
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
            day_index=day_index, phase=phase,
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
            phase=phase,
            day_index=day_index,
            slot=slot,
            old_food_id=old_food_id,
            new_food=new_food,
            foods=foods_map,
            targets=targets,
            config=config,
        )
        await persist_day_edit(
            plan_repo=repos.plans, cycle=cycle, phase=phase, day_index=day_index, day=day
        )
    except GenerationError:
        pass

    return await _edit_response(
        request, session, cycle=cycle, client_id=cycle.client_id,
        day_index=day_index, phase=phase,
    )


@router.post("/planes/{cycle_id}/dia/{day_index}/quitar", response_model=None)
async def remove_food(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)],
                      cycle_id: str, day_index: int,
                      fase: str = "first_15") -> Response:
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
    phase = await _phase_from_request(request, fase)

    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None or cycle.status != PlanStatus.DRAFT:
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
            phase=phase,
            day_index=day_index,
            slot=MealSlot(slot_value),
            food_id=food_id,
            foods=foods_map,
            targets=targets,
            config=config,
        )
        await persist_day_edit(
            plan_repo=repos.plans, cycle=cycle, phase=phase, day_index=day_index, day=day
        )
        if do_ban:
            await ban_for_client(
                client_repo=repos.clients, client_id=cycle.client_id, food_id=food_id
            )
    except (GenerationError, ValueError):
        pass

    return await _edit_response(
        request, session, cycle=cycle, client_id=cycle.client_id,
        day_index=day_index, phase=phase,
    )
