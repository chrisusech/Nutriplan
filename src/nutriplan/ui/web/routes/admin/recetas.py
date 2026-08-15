"""La biblioteca de recetas: lo que la app ha aprendido a cocinar.

Dos acciones, las dos en una dirección clara: **promover** lo que la gente
calificó bien al YAML curado (que va en git y sobrevive a la base), y **retirar**
lo que salió mal para que se vuelva a generar la próxima vez.
"""

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.meals.recipe_catalog_store import (
    RecipeCatalogError,
    append_recipe,
)
from nutriplan.application.recipe_library import (
    MIN_RATING_TO_PROMOTE,
    MIN_RATINGS_TO_PROMOTE,
    curated_from,
    is_promotable,
)
from nutriplan.domain.errors import ValidationError
from nutriplan.ui.web.deps import container_of, db_session, render, repos_of

router = APIRouter()

_SOURCES = ("ai", "curated", "yaml")


@router.get("/admin/recetas", response_class=HTMLResponse)
async def library(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    origen: str = "",
    error: str = "",
    ok: str = "",
) -> HTMLResponse:
    repo = container_of(request).dish_recipe_repo(session)
    source = origen if origen in _SOURCES else None
    recetas = await repo.library(source=source)
    return render(
        request,
        "admin_recetas.html",
        active_tab="recetas",
        recetas=[(r, is_promotable(r)) for r in recetas],
        origen=source or "",
        origenes=_SOURCES,
        min_notas=MIN_RATINGS_TO_PROMOTE,
        min_nota=MIN_RATING_TO_PROMOTE,
        curadas=len(container_of(request).recipe_catalog.recipes),
        error=error,
        ok=ok,
    )


@router.post("/admin/recetas/recalcular", response_model=None)
async def refresh_quality(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> RedirectResponse:
    """Vuelve a contar notas y veces servida de toda la biblioteca."""
    tocadas = await container_of(request).dish_recipe_repo(session).refresh_all()
    await session.commit()
    return _back(ok=f"Recalculadas {tocadas} recetas con las notas de la gente.")


@router.post("/admin/recetas/{dish_key}/promover", response_model=None)
async def promote_recipe(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dish_key: str,
) -> RedirectResponse:
    container = container_of(request)
    recipe = await container.dish_recipe_repo(session).get(dish_key)
    if recipe is None:
        return _back(error="Esa receta ya no está.")

    foods = {f.id: f for f in await repos_of(request, session).foods.get_by_ids(recipe.food_ids)}
    try:
        entry = curated_from(recipe, foods)
        added = append_recipe(container.settings.recipes_catalog_path, entry)
    except (ValidationError, RecipeCatalogError) as exc:
        return _back(error=str(exc))

    container.reload_recipe_catalog()
    return _back(
        ok=f"«{recipe.name_es}» ya está en el catálogo curado."
        if added
        else "Esa receta ya estaba en el catálogo."
    )


@router.post("/admin/recetas/{dish_key}/retirar", response_model=None)
async def retire_recipe(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dish_key: str,
) -> RedirectResponse:
    await container_of(request).dish_recipe_repo(session).retire(dish_key)
    await session.commit()
    return _back(ok="Retirada. Se volverá a escribir la próxima vez que salga.")


def _back(*, error: str = "", ok: str = "") -> RedirectResponse:
    query = urlencode({k: v for k, v in (("error", error), ("ok", ok)) if v})
    return RedirectResponse(
        f"/admin/recetas?{query}" if query else "/admin/recetas", status_code=303
    )
