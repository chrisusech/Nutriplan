"""Comer fuera y cambiar un plato: editan un día ya generado, sin gastar semana."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.llm.offline_engine import build_offline_engine
from nutriplan.adapters.llm.template_selector import TemplateSelector
from nutriplan.application.eating_out import apply_eating_out, resolve_dish
from nutriplan.application.swap_meal import swap_slot
from nutriplan.application.swap_wish import interpret_swap_wish
from nutriplan.application.taste_profile import taste_profile_for
from nutriplan.container import Repos
from nutriplan.domain.errors import GenerationError, ValidationError
from nutriplan.domain.models import Client, DayPlan, MealEntry, MealSlot, PlanCycle
from nutriplan.domain.restaurant import is_eating_out
from nutriplan.domain.swap_note import dish_from_foods, foods_from_note, has_technique
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    repos_of,
)

router = APIRouter()


def _back_to_meal(
    dia: int,
    slot: str,
    *,
    error: str | None = "",
    aviso: str | None = "",
) -> RedirectResponse:
    """Vuelve al día y a ESA comida abierta, no al desayuno."""
    extra = ""
    if error:
        extra += f"&error={quote(error)}"
    if aviso:
        extra += f"&aviso={quote(aviso)}"
    return RedirectResponse(
        f"/?dia={dia}&nota={quote(slot)}{extra}#nota-{slot}",
        status_code=303,
    )


def _slot(raw: str) -> MealSlot:
    try:
        return MealSlot(raw)
    except ValueError as exc:
        raise ValidationError("Esa comida no existe.") from exc


async def _day_context(
    request: Request, session: AsyncSession, dia: int, slot: MealSlot
) -> tuple[Client, PlanCycle, DayPlan, MealEntry, Repos]:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None or client.active_plan_id is None:
        raise ValidationError("Todavía no tienes un menú.")
    plan = await repos.plans.get(client.active_plan_id)
    if plan is None:
        raise ValidationError("Todavía no tienes un menú.")
    day = next((d for d in plan.days if d.day_index == dia), None)
    if day is None:
        raise ValidationError("Ese día no está en tu semana.")
    meal = next((m for m in day.meals if m.slot is slot), None)
    if meal is None:
        raise ValidationError("Esa comida no está en tu día.")
    return client, plan, day, meal, repos


@router.get("/menu/fuera", response_model=None)
async def eating_out_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dia: int = 0,
    slot: str = "cena",
    q: str = "",
    resto: str = "",
) -> HTMLResponse | RedirectResponse:
    try:
        chosen = _slot(slot)
        client, _plan, day, _meal, repos = await _day_context(request, session, dia, chosen)
    except ValidationError as exc:
        return RedirectResponse(f"/?error={quote(str(exc))}", status_code=303)
    catalog = container_of(request).restaurant_catalog
    restaurant = catalog.restaurant(resto) if resto else None
    hits = catalog.search(q, limit=60) if q else []
    eaten = [m for m in day.meals if m.eaten]
    consumidas = round(sum(m.computed.kcal for m in eaten))
    targets = await repos.targets.latest_for_client(client.id)
    objetivo = round(targets.daily.kcal) if targets else 0
    return render(
        request,
        "comer_fuera.html",
        active_tab="semana",
        dia=dia,
        slot=chosen.value,
        q=q,
        resto=restaurant,
        restaurantes=catalog.restaurants,
        hits=hits,
        consumidas=consumidas,
        restante=max(0, objetivo - consumidas),
    )


@router.post("/menu/fuera", response_model=None)
async def eating_out_save(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dia: Annotated[int, Form()],
    slot: Annotated[str, Form()],
    restaurant_id: Annotated[str, Form()],
    dish_id: Annotated[str, Form()],
    servings: Annotated[str, Form()] = "1",
) -> RedirectResponse:
    try:
        n = float(servings.replace(",", ".") or "1")
        n = min(max(n, 0.5), 4.0)
        chosen = _slot(slot)
        client, plan, day, _meal, repos = await _day_context(request, session, dia, chosen)
        catalog = container_of(request).restaurant_catalog
        name, dish = resolve_dish(catalog, restaurant_id, dish_id)
        foods = {f.id: f for f in await repos.foods.list_universe()}
        targets = await repos.targets.latest_for_client(client.id)
        if targets is None:
            raise ValidationError("Todavía no tienes calorías calculadas.")
        new_day, warning = apply_eating_out(
            day,
            slot=chosen,
            restaurant_id=restaurant_id,
            restaurant_name=name,
            dish=dish,
            foods_by_id=foods,
            daily=targets.daily,
            config=container_of(request).nutrition_config(client),
            servings=n,
        )
        await repos.plans.update_day(plan.id, dia, new_day, mark_edited=True)
    except (ValidationError, GenerationError, ValueError) as exc:
        return _back_to_meal(dia, slot, error=str(exc))
    return _back_to_meal(dia, chosen.value, aviso=warning)


@router.get("/menu/cambiar", response_model=None)
async def swap_page(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dia: int = 0,
    slot: str | None = None,
) -> HTMLResponse | RedirectResponse:
    if not slot:
        return RedirectResponse(
            f"/?error={quote('Elige la comida que quieres cambiar.')}",
            status_code=303,
        )
    try:
        chosen = _slot(slot)
        _client, _plan, _day, meal, _repos = await _day_context(request, session, dia, chosen)
    except ValidationError as exc:
        return RedirectResponse(f"/?error={quote(str(exc))}", status_code=303)
    if is_eating_out(meal) or meal.is_free_meal:
        aviso = quote("Ese plato es de restaurante: cámbialo desde Voy a comer fuera.")
        return RedirectResponse(f"/?dia={dia}&error={aviso}", status_code=303)
    return render(
        request,
        "cambiar_plato.html",
        active_tab="semana",
        dia=dia,
        slot=chosen.value,
        plato=meal.dish_name,
    )


@router.post("/menu/cambiar", response_model=None)
async def swap_save(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dia: Annotated[int, Form()],
    slot: Annotated[str, Form()],
    nota: Annotated[str, Form()] = "",
) -> RedirectResponse:
    try:
        chosen = _slot(slot)
        client, plan, day, meal, repos = await _day_context(request, session, dia, chosen)
        if is_eating_out(meal) or meal.is_free_meal:
            raise ValidationError("Ese plato es de calle.")
        targets = await repos.targets.latest_for_client(client.id)
        if targets is None:
            raise ValidationError("Todavía no tienes calorías calculadas.")
        foods = await repos.foods.list_universe()
        foods_by_id = {f.id: f for f in foods}
        container = container_of(request)
        taste = await taste_profile_for(client=client, ratings=repos.ratings, signals=repos.taste)
        engine = build_offline_engine(
            allowed=foods,
            daily=targets.daily,
            config=container.nutrition_config(client),
            seed=plan.variant,
            catalog=container.meal_catalog,
            taste=taste,
        )
        if not isinstance(engine, TemplateSelector):
            raise ValidationError("No hay otro plato que encaje en esa comida.")
        today_ids = frozenset(
            str(i.food_id) for m in day.meals if m.slot is not chosen for i in m.items if i.food_id
        )
        exclude = frozenset(filter(None, [meal.dish_key]))
        wish = nota.strip()[:400]
        pick = None
        # Si pidió una técnica (sudado, plancha…), la IA nombra el plato.
        # El ranking solo ve alimentos y devolvería «pechuga a la plancha».
        if wish and has_technique(wish):
            pick = await interpret_swap_wish(
                note=wish,
                slot=chosen,
                allowed=foods,
                llm=container.llm_client,
                prompts_dir=container.settings.prompts_dir,
                model=container.settings.llm_model_generate,
            )
        if pick is None:
            ranked = engine.rank_slot(chosen, exclude_keys=exclude, today_ids=today_ids, note=wish)
            pick = ranked[0] if ranked else None
        if pick is None and wish:
            pick = dish_from_foods(chosen, foods_from_note(wish, foods))
        if pick is None and wish and not has_technique(wish):
            pick = await interpret_swap_wish(
                note=wish,
                slot=chosen,
                allowed=foods,
                llm=container.llm_client,
                prompts_dir=container.settings.prompts_dir,
                model=container.settings.llm_model_generate,
            )
        if pick is None:
            if wish:
                raise ValidationError(
                    f"No tenemos un plato que encaje con «{wish[:80]}» en esa comida."
                )
            raise ValidationError("No hay otro plato que encaje en esa comida.")
        new_day = swap_slot(
            day,
            slot=chosen,
            pick=pick,
            foods_by_id=foods_by_id,
            daily=targets.daily,
            config=container.nutrition_config(client),
        )
        await repos.plans.update_day(plan.id, dia, new_day, mark_edited=True)
    except (ValidationError, GenerationError) as exc:
        return _back_to_meal(dia, slot, error=str(exc))
    return _back_to_meal(dia, chosen.value)
