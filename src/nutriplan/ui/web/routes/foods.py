"""Mis alimentos: la despensa que la persona puede corregir a mano.

El onboarding pregunta una vez y con prisa. Aquí se arregla lo que salió mal
—"no vuelvo a ver la lenteja", "el salmón sí lo quiero"— sin regenerar nada:
lo que se toca aquí es lo que la semana siguiente tiene permitido usar.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.food_pool import resolve_allowed_foods
from nutriplan.application.my_foods import add_food, addable, remove_food
from nutriplan.domain.models import FoodItem
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import account_id_of, db_session, render, repos_of

router = APIRouter()


def grouped(foods: list[FoodItem], *, marked: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Por categoría, en el mismo orden que el onboarding: se reconoce la lista.

    `marked` son los ids que la pantalla debe mostrar señalados —hoy, lo que la
    persona dice tener en casa—. Vive aquí porque las dos pantallas de alimentos
    tienen que agrupar igual: si una ordena distinto, deja de parecer la misma
    lista.
    """
    groups: list[dict[str, Any]] = []
    for meta in presenter.FOOD_GROUPS:
        items = [
            {"id": str(f.id), "name": f.name_es.capitalize(), "marcado": str(f.id) in marked}
            for f in sorted(foods, key=lambda f: f.name_es)
            if f.category == meta["cat"]
        ]
        if items:
            marcados = sum(1 for i in items if i["marcado"])
            groups.append({**meta, "items": items, "marcados": marcados})
    return groups


@router.get("/mis-alimentos", response_model=None)
async def my_foods(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)

    allowed = await resolve_allowed_foods(client, food_repo=repos.foods, client_repo=repos.clients)
    universe = await repos.foods.list_universe()
    return render(
        request,
        "mis_alimentos.html",
        active_tab="perfil",
        perfil=client,
        dentro=grouped(allowed),
        fuera=grouped(
            addable(universe=universe, allowed=allowed, restrictions=client.restrictions)
        ),
        n_dentro=len(allowed),
    )


@router.post("/mis-alimentos", response_model=None)
async def toggle_food(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    food_id: Annotated[str, Form()],
    accion: Annotated[str, Form()],
) -> RedirectResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)
    try:
        target = UUID(food_id)
    except ValueError:
        return RedirectResponse("/mis-alimentos", status_code=303)

    found = await repos.foods.get_by_ids([target])
    if not found:
        return RedirectResponse("/mis-alimentos", status_code=303)

    if accion == "quitar":
        await remove_food(client=client, food=found[0], client_repo=repos.clients)
    else:
        await add_food(client=client, food=found[0], client_repo=repos.clients)
    await session.commit()
    return RedirectResponse("/mis-alimentos", status_code=303)
