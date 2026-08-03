"""Mi semana: la pantalla que la gente abre a diario.

Reemplaza al dashboard de clientes del entrenador. Aquí solo hay una persona
—quien está mirando— y su menú.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.analytics import Event
from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.models import MealSlot, PlanCycle
from nutriplan.ui.web import week_view
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    repos_of,
    track_event,
)

router = APIRouter()


def _safe_day(raw: str | None) -> int:
    try:
        return min(max(int(raw or 0), 0), 6)
    except ValueError:
        return 0


async def _recipes_for(
    request: Request, session: AsyncSession, plan: PlanCycle
) -> dict[str, DishRecipe]:
    keys = [m.dish_key for d in plan.days for m in d.meals if m.dish_key]
    if not keys:
        return {}
    return await container_of(request).dish_recipe_repo(session).get_many(keys)


@router.get("/", response_model=None)
async def my_week(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)

    plan = (
        await repos.plans.get(client.active_plan_id) if client.active_plan_id else None
    )
    if plan is None:
        return render(request, "week.html", active_tab="semana", plan=None)

    dia = _safe_day(request.query_params.get("dia"))
    day = next((d for d in plan.days if d.day_index == dia), None)
    if day is None:
        return render(request, "week.html", active_tab="semana", plan=None)

    ids = sorted({i.food_id for m in day.meals for i in m.items if i.food_id}, key=str)
    foods = {f.id: f for f in await repos.foods.get_by_ids(ids)}
    recipes = await _recipes_for(request, session, plan)
    ratings = await repos.ratings.for_day(plan.id, dia)

    quota = await repos.ratings.quota_for(plan.id)
    return render(
        request,
        "week.html",
        active_tab="semana",
        plan=plan,
        dia=dia,
        days=week_view.day_chips(plan, dia),
        day=week_view.day_view(day, foods, recipes, ratings),
        can_regenerate=quota.unlocked,
        unlock_hint=quota.hint,
    )


@router.post("/calificar", response_model=None)
async def rate_meal(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dia: Annotated[int, Form()],
    slot: Annotated[str, Form()],
    rating: Annotated[int, Form()],
) -> RedirectResponse:
    """Calificar un plato. Es el dato que justifica el BETA entero."""
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None or client.active_plan_id is None:
        return RedirectResponse("/", status_code=303)

    plan = await repos.plans.get(client.active_plan_id)
    if plan is None:
        return RedirectResponse("/", status_code=303)

    day = next((d for d in plan.days if d.day_index == _safe_day(str(dia))), None)
    meal = next((m for m in day.meals if m.slot.value == slot), None) if day else None
    if day is None or meal is None:
        return RedirectResponse(f"/?dia={dia}", status_code=303)

    await repos.ratings.rate(
        user_id=account_id_of(request),
        plan_cycle_id=plan.id,
        day_index=day.day_index,
        slot=MealSlot(slot),
        template_id=meal.template_id,
        dish_key=meal.dish_key,
        rating=min(max(rating, 1), 5),
    )
    await track_event(
        request, session, Event.DISH_RATED,
        plantilla=meal.template_id, nota=min(max(rating, 1), 5),
        dia=day.day_index, comida=slot,
    )
    return RedirectResponse(f"/?dia={dia}", status_code=303)



