"""Lista de compra de la semana: lo que hay que llevar al súper."""

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.shopping_list import shopping_list_for_plan
from nutriplan.domain.models import DAYS_PER_WEEK
from nutriplan.ui.web.deps import account_id_of, db_session, render, repos_of

router = APIRouter()


@router.get("/compra", response_model=None)
async def shopping_list(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)
    if client.active_plan_id is None:
        return RedirectResponse("/", status_code=303)

    plan = await repos.plans.get(client.active_plan_id)
    if plan is None:
        return RedirectResponse("/", status_code=303)

    ids = sorted(
        {
            i.food_id
            for d in plan.days
            for m in d.meals
            for i in m.items
            if i.food_id and i.grams and not m.is_free_meal
        },
        key=str,
    )
    foods = {f.id: f for f in await repos.foods.get_by_ids(ids)}
    # Lo que dijo tener en casa cuando se generó ESTA semana, no lo de hoy: la
    # lista tiene que cuadrar con el menú que se ve al lado.
    en_casa = set(await repos.clients.list_pantry_food_ids(client.id, plan.week_start))
    groups = shopping_list_for_plan(plan, foods, en_casa=en_casa)
    return render(
        request,
        "shopping.html",
        active_tab="compra",
        groups=groups,
        # El titular cuenta lo que hay que COMPRAR: si dijo tener seis cosas, la
        # compra es más corta y eso es justo lo que se quiere ver.
        n_items=sum(1 for g in groups for ln in g.lines if not ln.ya_tengo),
        n_en_casa=sum(1 for g in groups for ln in g.lines if ln.ya_tengo),
        # Qué semana se está comprando, para que nadie crea que es la de hoy.
        desde=plan.week_start,
        hasta=plan.week_start + timedelta(days=DAYS_PER_WEEK - 1),
        # Se llega aquí recién generada la semana: hay que celebrarlo y ofrecer la
        # puerta al menú, o parece que el botón de generar lleva al súper.
        nueva=request.query_params.get("nueva") == "1",
    )
