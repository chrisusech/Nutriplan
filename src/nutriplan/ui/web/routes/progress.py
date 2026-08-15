"""Mi progreso: qué pesaba, con cuántas kcal y qué menú vivió cada semana."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.progress import build_progress, daily_kcal
from nutriplan.domain.week_recap import week_recap
from nutriplan.ui.web.chart import barras
from nutriplan.ui.web.deps import account_id_of, db_session, render, repos_of

router = APIRouter()


@router.get("/progreso", response_model=None)
async def my_progress(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)

    plans = await repos.plans.list_for_client(client.id)
    weeks = build_progress(
        entries=await repos.weights.history(client.id),
        targets=await repos.targets.history(client.id),
        plans=plans,
        ratings=await repos.ratings.average_by_plan([p.id for p in plans]),
    )
    first = weeks[-1].weight_kg if weeks else None
    cronologico = list(reversed(weeks))
    comidas = [float(w.kcal_eaten) for w in cronologico if w.kcal_eaten is not None]
    grafica_kcal = barras(comidas)
    plan = next((p for p in plans if client.active_plan_id and p.id == client.active_plan_id), None)
    targets_now = await repos.targets.latest_for_client(client.id)
    dias = daily_kcal(plan, targets_now.daily) if plan and targets_now else []
    recap = week_recap(plan, targets_now.daily) if plan and targets_now else None
    return render(
        request,
        "progreso.html",
        active_tab="progreso",
        perfil=client,
        semanas=weeks,
        # La lista llega de la más reciente a la más antigua; la gráfica se lee
        # al revés, como pasa el tiempo.
        grafica=barras([w.weight_kg for w in cronologico]),
        grafica_kcal=grafica_kcal,
        grafica_dias=barras([float(d.kcal) for d in dias]) if dias else [],
        dias=dias,
        recap=recap,
        total_delta=(round(weeks[0].weight_kg - first, 1) if weeks and first is not None else None),
    )
