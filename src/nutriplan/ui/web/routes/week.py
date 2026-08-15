"""Mi semana: la pantalla que la gente abre a diario.

Reemplaza al dashboard de clientes del entrenador. Aquí solo hay una persona
—quien está mirando— y su menú.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.analytics import Event
from nutriplan.application.auto_week import promote_current_week
from nutriplan.application.mark_eaten import mark_eaten
from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.errors import ValidationError
from nutriplan.domain.models import MealSlot, PlanCycle
from nutriplan.domain.streak import current_streak
from nutriplan.domain.week import iso_week_start, today_weekday
from nutriplan.ui.web import week_view
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    is_super_user,
    render,
    repos_of,
    track_event,
)
from nutriplan.ui.web.gate import week_gate

router = APIRouter()


def _safe_day(raw: str | None) -> int:
    try:
        return min(max(int(raw or 0), 0), 6)
    except ValueError:
        return 0


def _open_day(raw: str | None) -> int:
    """Sin `?dia=` se abre hoy. Un valor raro se acota; no se vuelve al lunes."""
    if raw is None:
        return today_weekday()
    return _safe_day(raw)


async def _recipes_for_day(
    request: Request, session: AsyncSession, plan: PlanCycle, day_index: int
) -> dict[str, DishRecipe]:
    day = next((d for d in plan.days if d.day_index == day_index), None)
    if day is None:
        return {}
    keys = [m.dish_key for m in day.meals if m.dish_key]
    if not keys:
        return {}
    recipes = await container_of(request).dish_recipe_repo(session).get_many(keys)
    # No pintar plantillas yaml: el boot del día las sube a IA.
    return {k: r for k, r in recipes.items() if r.source != "yaml"}


@router.get("/", response_model=None)
async def my_week(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)
    client = await promote_current_week(client=client, plans=repos.plans, clients=repos.clients)

    plan = await repos.plans.get(client.active_plan_id) if client.active_plan_id else None
    gate = await week_gate(request, session, client, plan=plan)
    dia = _open_day(request.query_params.get("dia"))
    day = next((d for d in plan.days if d.day_index == dia), None) if plan else None
    # Cuántas cosas dijo tener en casa: la entrada a esa pantalla se acompaña del
    # número para que se vea que lo marcado sigue ahí.
    en_casa_n = len(await repos.clients.list_pantry_food_ids(client.id, iso_week_start()))
    if plan is None or day is None:
        # Sin plan la pantalla no se disculpa: celebra el perfil recién hecho y
        # ofrece el único botón que hay que tocar.
        return render(
            request,
            "week.html",
            active_tab="semana",
            plan=None,
            perfil=client,
            needs_checkin=gate.needs_checkin,
            membership=gate.membership,
            en_casa_n=en_casa_n,
        )

    ids = sorted({i.food_id for m in day.meals for i in m.items if i.food_id}, key=str)
    foods = {f.id: f for f in await repos.foods.get_by_ids(ids)}
    recipes = await _recipes_for_day(request, session, plan, dia)
    ratings = await repos.ratings.for_day(plan.id, dia)
    # Contra qué se compara lo comido: sin el objetivo no hay anillo que pintar.
    targets = await repos.targets.get(plan.targets_id)
    vista = week_view.day_view(day, foods, recipes, ratings)
    planes = await repos.plans.list_for_client(client.id)

    return render(
        request,
        "week.html",
        active_tab="semana",
        plan=plan,
        dia=dia,
        days=week_view.day_chips(plan, dia),
        day=vista,
        progreso=week_view.day_progress(vista, targets.daily if targets else None),
        racha=current_streak(planes),
        can_regenerate=gate.allowed and is_super_user(request),
        waiting_sunday=gate.reason.startswith("El domingo"),
        needs_checkin=gate.needs_checkin,
        unlock_hint=gate.reason,
        closure=gate.closure,
        membership=gate.membership,
        en_casa_n=en_casa_n,
        # La comida que se queda abierta al volver de calificar: sin esto la
        # tarjeta se cierra al recargar, la pregunta por el «por qué» no llega a
        # verse y en el beta salieron 27 notas sin un solo comentario.
        abierta=request.query_params.get("nota", ""),
        aviso=request.query_params.get("aviso", ""),
        error=request.query_params.get("error", ""),
    )


async def _comi_swap(
    request: Request,
    session: AsyncSession,
    plan: PlanCycle,
    day_index: int,
    *,
    slot: MealSlot,
) -> HTMLResponse:
    """El botón y el anillo (OOB). Así las kcal nuevas no dependen del JS."""
    repos = repos_of(request, session)
    day = next((d for d in plan.days if d.day_index == day_index), None)
    if day is None:
        return render(
            request,
            "partials/meal_eaten.html",
            dia=day_index,
            meal={"slot": slot.value, "eaten": False},
        )
    ids = sorted({i.food_id for m in day.meals for i in m.items if i.food_id}, key=str)
    foods = {f.id: f for f in await repos.foods.get_by_ids(ids)}
    recipes = await _recipes_for_day(request, session, plan, day_index)
    ratings = await repos.ratings.for_day(plan.id, day_index)
    targets = await repos.targets.get(plan.targets_id)
    vista = week_view.day_view(day, foods, recipes, ratings)
    meal_vm = next((m for m in vista["meals"] if m["slot"] == slot.value), None)
    if meal_vm is None:
        meal_vm = {"slot": slot.value, "eaten": False}
    client = await repos.clients.get_by_user(account_id_of(request))
    planes = await repos.plans.list_for_client(client.id) if client else [plan]
    return render(
        request,
        "partials/comi_swap.html",
        dia=day_index,
        day=vista,
        progreso=week_view.day_progress(vista, targets.daily if targets else None),
        racha=current_streak(planes),
        meal=meal_vm,
    )


@router.post("/menu/comi", response_model=None)
async def mark_meal_eaten(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dia: Annotated[int, Form()],
    slot: Annotated[str, Form()],
    eaten: Annotated[str, Form()] = "1",
) -> HTMLResponse | RedirectResponse:
    """Marca (o desmarca) una comida. El anillo cuenta solo lo marcado."""
    htmx = _htmx(request)
    day_index = _safe_day(str(dia))
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None or client.active_plan_id is None:
        return _go("/", htmx=htmx)
    plan = await repos.plans.get(client.active_plan_id)
    if plan is None:
        return _go("/", htmx=htmx)
    day = next((d for d in plan.days if d.day_index == day_index), None)
    try:
        chosen = MealSlot(slot)
    except ValueError:
        return _go(f"/?dia={day_index}", htmx=htmx)
    if day is None:
        return _go(f"/?dia={day_index}", htmx=htmx)
    try:
        nuevo = mark_eaten(day, chosen, eaten != "0")
    except ValidationError:
        return _go(f"/?dia={day_index}", htmx=htmx)
    await repos.plans.update_day(plan.id, day_index, nuevo)
    await session.commit()
    if htmx:
        plan = await repos.plans.get(client.active_plan_id)
        if plan is None:
            return _go(f"/?dia={day_index}", htmx=htmx)
        return await _comi_swap(request, session, plan, day_index, slot=chosen)
    return RedirectResponse(f"/?dia={day_index}", status_code=303)


def _htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _go(url: str, *, htmx: bool) -> RedirectResponse:
    resp = RedirectResponse(url, status_code=303)
    if htmx:
        # Si no, htmx metería el HTML entero de la semana dentro del formulario.
        resp.headers["HX-Redirect"] = url
    return resp


async def _rate_form(
    request: Request,
    session: AsyncSession,
    plan: PlanCycle,
    day_index: int,
    slot: str,
) -> HTMLResponse:
    """El formulario de estrellas, ya con la nota puesta. Lo pide HTMX."""
    repos = repos_of(request, session)
    day = next((d for d in plan.days if d.day_index == day_index), None)
    if day is None:
        meal_vm: dict[str, object] = {"slot": slot}
        return render(request, "partials/rate_form.html", meal=meal_vm, dia=day_index, abierta=slot)
    ids = sorted({i.food_id for m in day.meals for i in m.items if i.food_id}, key=str)
    foods = {f.id: f for f in await repos.foods.get_by_ids(ids)}
    recipes = await _recipes_for_day(request, session, plan, day_index)
    ratings = await repos.ratings.for_day(plan.id, day_index)
    vista = week_view.day_view(day, foods, recipes, ratings)
    meal_vm = next((m for m in vista["meals"] if m["slot"] == slot), {"slot": slot})
    return render(
        request,
        "partials/rate_form.html",
        meal=meal_vm,
        dia=day_index,
        abierta=slot,
    )


@router.post("/calificar", response_model=None)
async def rate_meal(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dia: Annotated[int, Form()],
    slot: Annotated[str, Form()],
    rating: Annotated[int | None, Form()] = None,
    comment: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Calificar un plato. Comentario opcional por plato (no el general de Opinar).

    HTMX cambia solo el formulario: un POST clásico recargaba toda la semana y
    en iOS eso era un fogonazo negro entre páginas.
    """
    htmx = _htmx(request)
    if rating is None:
        return _go(f"/?dia={dia}", htmx=htmx)

    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None or client.active_plan_id is None:
        return _go("/", htmx=htmx)

    plan = await repos.plans.get(client.active_plan_id)
    if plan is None:
        return _go("/", htmx=htmx)

    day = next((d for d in plan.days if d.day_index == _safe_day(str(dia))), None)
    meal = next((m for m in day.meals if m.slot.value == slot), None) if day else None
    if day is None or meal is None:
        return _go(f"/?dia={dia}", htmx=htmx)

    nota = min(max(rating, 1), 5)
    await repos.ratings.rate(
        user_id=account_id_of(request),
        plan_cycle_id=plan.id,
        day_index=day.day_index,
        slot=MealSlot(slot),
        template_id=meal.template_id,
        dish_key=meal.dish_key,
        rating=nota,
        comment=comment[:400],
    )
    # La nota es lo que no puede perderse: se confirma ya, y el agregado de la
    # biblioteca + el evento van en otra transacción. Si se queda abierta, un
    # segundo POST (estrella + comentario) espera el lock de SQLite y muere.
    await session.commit()
    # La biblioteca aprende aquí: una nota más cambia la nota del plato para
    # todo el mundo, no solo para quien la puso.
    if meal.dish_key:
        await container_of(request).dish_recipe_repo(session).refresh_quality([meal.dish_key])
    await track_event(
        request,
        session,
        Event.DISH_RATED,
        plantilla=meal.template_id,
        nota=nota,
        dia=day.day_index,
        comida=slot,
        con_comentario=bool(comment.strip()),
    )
    if htmx:
        return await _rate_form(request, session, plan, day.day_index, slot)
    # Sin HTMX se vuelve a la misma comida y abierta: la nota es media
    # respuesta, y la otra media —por qué— solo se escribe si la caja sigue
    # delante al recargar.
    return RedirectResponse(f"/?dia={dia}&nota={slot}#nota-{slot}", status_code=303)
