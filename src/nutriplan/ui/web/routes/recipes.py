"""Recetas (Workstream H): el entrenador las sube, el admin las verifica."""

from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.recipes import (
    create_recipe,
    create_recipe_from_macros,
    reject_recipe,
    verify_recipe,
)
from nutriplan.domain.errors import ValidationError
from nutriplan.domain.models import (
    MacroTargets,
    MealSlot,
    RecipeIngredient,
    RecipeStatus,
)
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import (
    container_of,
    db_session,
    render,
    repos_of,
    safe_uuid,
    tenant_of,
)

router = APIRouter()

_STATUS_LABEL = {
    RecipeStatus.PENDING: ("Pendiente", "#C9852E", "#FBF0DA"),
    RecipeStatus.VERIFIED: ("Verificada", "#2E9E6E", "#E3F4EA"),
    RecipeStatus.REJECTED: ("Rechazada", "#C0554A", "#FBE7E3"),
}


def _recipe_card(recipe: Any) -> dict[str, Any]:
    label, color, soft = _STATUS_LABEL[recipe.status]
    return {
        "id": str(recipe.id),
        "name": recipe.name,
        "status_label": label,
        "status_color": color,
        "status_soft": soft,
        "kcal": round(recipe.macros.kcal),
        "protein": round(recipe.macros.protein_g),
        "carb": round(recipe.macros.carb_g),
        "fat": round(recipe.macros.fat_g),
        "total_grams": round(recipe.total_grams),
        "n_ingredients": len(recipe.ingredients),
        # Sin ingredientes no es una receta a medio hacer: es un plato del que se
        # declararon los macros. La tarjeta tiene que decirlo, no poner "0".
        "from_macros": not recipe.ingredients,
        "slots": [presenter.SLOT_META[s]["name"] for s in recipe.meal_slots],
    }


@router.get("/recetas", response_class=HTMLResponse)
async def recipes_index(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)]) -> HTMLResponse:
    repos = repos_of(request, session)
    recipes = await repos.recipes.list_for_tenant()
    universe = await repos.foods.list_universe()
    groups = []
    for meta in presenter.FOOD_GROUPS:
        items = [
            {"id": str(f.id), "name": f.name_es.capitalize()}
            for f in sorted(universe, key=lambda f: f.name_es)
            if f.category == meta["cat"]
        ]
        if items:
            groups.append({**meta, "items": items})
    return render(request, "recipes.html", active_tab="recetas",
                  cards=[_recipe_card(r) for r in recipes], groups=groups,
                  slots=[(s.value, presenter.SLOT_META[s]["name"]) for s in MealSlot],
                  error=request.query_params.get("error"))


def _macro(form: Any, key: str) -> float:
    raw = str(form.get(key, "")).strip().replace(",", ".")
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return 0.0


@router.post("/recetas")
async def create(request: Request,
                 session: Annotated[AsyncSession, Depends(db_session)]) -> RedirectResponse:
    repos = repos_of(request, session)
    form = await request.form()
    name = str(form.get("name", ""))
    slots = [MealSlot(str(s)) for s in form.getlist("meal_slot")]

    try:
        if str(form.get("modo", "")) == "macros":
            # El plato de restaurante: no sabemos qué lleva, sabemos qué aporta.
            await create_recipe_from_macros(
                tenant_id=tenant_of(request),
                name=name,
                macros=MacroTargets(
                    kcal=_macro(form, "kcal"),
                    protein_g=_macro(form, "protein_g"),
                    carb_g=_macro(form, "carb_g"),
                    fat_g=_macro(form, "fat_g"),
                ),
                total_grams=_macro(form, "total_grams"),
                meal_slots=slots,
                recipe_repo=repos.recipes,
            )
        else:
            ingredients: list[RecipeIngredient] = []
            for fid in form.getlist("food_id"):
                raw_g = str(form.get(f"grams_{fid}", "")).strip()
                try:
                    grams = float(raw_g)
                except ValueError:
                    continue
                if grams > 0:
                    ingredients.append(
                        RecipeIngredient(food_id=UUID(str(fid)), grams=grams)
                    )
            await create_recipe(
                tenant_id=tenant_of(request), name=name, ingredients=ingredients,
                food_repo=repos.foods, recipe_repo=repos.recipes, meal_slots=slots,
            )
    except ValidationError as exc:
        # Antes esto era un `pass`. En una pantalla donde el entrenador teclea
        # números a mano, tragarse el error significa que el plato desaparece sin
        # decir por qué y él lo vuelve a escribir igual.
        return RedirectResponse(f"/recetas?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/recetas", status_code=303)


# --- Cola de verificación (admin) ------------------------------------------


@router.get("/admin/recetas", response_class=HTMLResponse)
async def admin_recipes(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)]) -> HTMLResponse:
    container = container_of(request)
    pending = await container.admin_recipe_repo(session).list_pending()
    return render(request, "admin_recipes.html", active_tab="recetas",
                  cards=[_recipe_card(r) for r in pending])


@router.post("/admin/recetas/{recipe_id}/verificar")
async def verify(request: Request,
                 session: Annotated[AsyncSession, Depends(db_session)],
                 recipe_id: str) -> RedirectResponse:
    container = container_of(request)
    admin_repo = container.admin_recipe_repo(session)
    recipe = await admin_repo.get(safe_uuid(recipe_id))
    if recipe is not None:
        # el alimento compuesto se crea en el tenant DUEÑO de la receta
        owner_foods = container.repos(session, recipe.tenant_id).foods
        await verify_recipe(recipe_id=recipe.id, recipe_repo=admin_repo, food_repo=owner_foods)
    return RedirectResponse("/admin/recetas", status_code=303)


@router.post("/admin/recetas/{recipe_id}/rechazar")
async def reject(request: Request,
                 session: Annotated[AsyncSession, Depends(db_session)],
                 recipe_id: str) -> RedirectResponse:
    admin_repo = container_of(request).admin_recipe_repo(session)
    await reject_recipe(recipe_id=safe_uuid(recipe_id), recipe_repo=admin_repo)
    return RedirectResponse("/admin/recetas", status_code=303)
