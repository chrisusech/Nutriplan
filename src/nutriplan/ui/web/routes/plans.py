"""Planes: listado, revisión y aprobación (pantalla 1d), export y edición."""

import unicodedata
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.approve_plan import approve_plan, reopen_plan
from nutriplan.application.export_plan import export_plan
from nutriplan.domain.models import (
    MealFoodPortion,
    PlanStatus,
)
from nutriplan.domain.validation import day_totals
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import container_of, db_session, render, repos_of, tenant_of
from nutriplan.ui.web.routes.generator import _generator_context

router = APIRouter()

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


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
        assert targets is not None
    config = container.config_provider.get_nutrition_config()

    grid = presenter.week_grid(cycle, targets, config)
    fit_days = sum(1 for cell in grid if cell["fit"])
    approved = cycle.status == PlanStatus.APPROVED
    goal_meta = presenter.GOAL_META[client.goal]
    return render(
        request, "review.html", active_tab="planes",
        client=client, cycle=cycle, grid=grid, fit_days=fit_days,
        adh=presenter.adherence(cycle, targets), approved=approved,
        subtitle=(f"{goal_meta['label']} · {presenter.fmt_kcal(targets.daily.kcal)} kcal · "
                  f"7 días · generado {presenter.time_ago(cycle.created_at)}"),
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

    _, content = await export_plan(
        plan_id=cycle.id, fmt=fmt,  # type: ignore[arg-type]
        plan_repo=repos.plans, food_repo=repos.foods, artifact_repo=repos.artifacts,
        renderer=container.renderer_for(fmt), branding=container.branding(tenant_of(request)),
        exports_dir=container.settings.exports_dir,
        client_name=client.name if client else None,
    )
    who = (client.name if client else "cliente").replace(" ", "_")
    return Response(
        content=content, media_type=MEDIA_TYPES[fmt],
        headers={"Content-Disposition": _attachment(f"plan_semanal_{who}.{fmt}")},
    )


@router.post("/planes/{cycle_id}/dia/{day_index}/porciones", response_model=None)
async def edit_portions(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)],
                        cycle_id: str, day_index: int) -> Response:
    """Edición en línea: nuevos gramos → recálculo en código → persistir."""
    repos = repos_of(request, session)
    form = await request.form()
    slot_value = str(form.get("slot", ""))
    cliente = str(form.get("cliente", ""))

    cycle = await repos.plans.get(UUID(cycle_id))
    if cycle is None or cycle.status != PlanStatus.DRAFT:
        return RedirectResponse(f"/generador?cliente={cliente}", status_code=303)

    day = next(d for d in cycle.days if d.day_index == day_index)
    meal = next(m for m in day.meals if m.slot.value == slot_value)

    new_portions: list[MealFoodPortion] = []
    for p in meal.portions:
        raw = form.get(f"grams_{p.food_id}")
        grams = p.grams
        if raw is not None:
            try:
                grams = max(float(str(raw)), 0.0)
            except ValueError:
                grams = p.grams
        if grams > 0:
            new_portions.append(MealFoodPortion(food_id=p.food_id, grams=grams))

    if new_portions:  # una comida no puede quedar vacía
        foods = {f.id: f for f in await repos.foods.get_by_ids([p.food_id for p in new_portions])}
        meal.portions = new_portions
        meal.computed = presenter.compute_meal_macros(
            [(foods[p.food_id], p.grams) for p in new_portions]
        )
        day.totals = day_totals(list(day.meals))
        await repos.plans.update_days(cycle)

    client = await repos.clients.get(cycle.client_id)
    assert client is not None
    ctx = await _generator_context(request, session, client, day_index, editar=True)
    return render(request, "partials/generator_body.html", active_tab="generador", ctx=ctx)
