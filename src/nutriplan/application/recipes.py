"""Casos de uso de recetas (Workstream H).

Una receta = nombre + ingredientes (food_id + gramos). Los macros agregados los
calcula el DOMINIO (portioning.macros_of), nunca la IA. El entrenador la sube
(pending); un admin la verifica (verified). Al verificarla se materializa como
un 'alimento compuesto' del tenant: un FoodItem por-100g con el tamaño de la
receta como unidad, de modo que la generación existente puede seleccionarlo sin
tocar el solver.
"""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

import structlog

from nutriplan.domain.errors import ValidationError
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    Recipe,
    RecipeIngredient,
    RecipeStatus,
    UnitGranularity,
)
from nutriplan.domain.portioning import macros_of

logger = structlog.get_logger(__name__)

# kcal por gramo de cada macro (para elegir la categoría dominante del compuesto).
_KCAL_PER_G = {"protein": 4.0, "carb": 4.0, "fat": 9.0}


class RecipeRepository(Protocol):
    async def add(self, recipe: Recipe) -> None: ...
    async def get(self, recipe_id: UUID) -> Recipe | None: ...
    async def list_for_tenant(self) -> list[Recipe]: ...
    async def list_pending(self) -> list[Recipe]: ...
    async def set_status(
        self, recipe_id: UUID, status: RecipeStatus, *, compound_food_id: UUID | None = None
    ) -> None: ...


class FoodLookup(Protocol):
    async def get_by_ids(self, food_ids: list[UUID]) -> list[FoodItem]: ...
    async def add_custom(self, food: FoodItem) -> None: ...


async def create_recipe(
    *,
    tenant_id: UUID,
    name: str,
    ingredients: list[RecipeIngredient],
    food_repo: FoodLookup,
    recipe_repo: RecipeRepository,
    created_by: UUID | None = None,
) -> Recipe:
    """Crea una receta pendiente con macros agregados calculados por el dominio."""
    name = name.strip()
    if not name:
        raise ValidationError("La receta necesita un nombre")
    if not ingredients:
        raise ValidationError("La receta necesita al menos un ingrediente")

    foods = {f.id: f for f in await food_repo.get_by_ids([i.food_id for i in ingredients])}
    missing = [str(i.food_id) for i in ingredients if i.food_id not in foods]
    if missing:
        raise ValidationError(f"Ingredientes fuera del catálogo: {', '.join(missing)}")

    portions = [(foods[i.food_id], i.grams) for i in ingredients]
    macros = macros_of(portions)
    total_grams = sum(i.grams for i in ingredients)

    recipe = Recipe(
        id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        ingredients=ingredients,
        macros=macros,
        total_grams=total_grams,
        status=RecipeStatus.PENDING,
        created_by=created_by,
        created_at=datetime.now(UTC),
    )
    await recipe_repo.add(recipe)
    logger.info("recipe_created", recipe_id=str(recipe.id), tenant_id=str(tenant_id))
    return recipe


def _dominant_category(macros: MacroTargets) -> FoodCategory:
    by_kcal = {
        FoodCategory.PROTEIN: macros.protein_g * _KCAL_PER_G["protein"],
        FoodCategory.CARB: macros.carb_g * _KCAL_PER_G["carb"],
        FoodCategory.FAT: macros.fat_g * _KCAL_PER_G["fat"],
    }
    return max(by_kcal, key=lambda c: by_kcal[c])


def _compound_food(recipe: Recipe) -> FoodItem:
    """Materializa la receta verificada como alimento compuesto (por-100g).

    El tamaño total de la receta es la unidad: se cuenta en porciones enteras
    ('1 porción (Xg)'), así el solver la trata como un ítem contable coherente.
    """
    g = recipe.total_grams or 1.0
    per100 = 100.0 / g
    return FoodItem(
        id=uuid4(),
        tenant_id=recipe.tenant_id,
        source="recipe",
        source_ref=str(recipe.id),
        name_es=recipe.name,
        category=_dominant_category(recipe.macros),
        kcal_100g=round(recipe.macros.kcal * per100, 2),
        protein_100g=round(recipe.macros.protein_g * per100, 2),
        carb_100g=round(recipe.macros.carb_g * per100, 2),
        fat_100g=round(recipe.macros.fat_g * per100, 2),
        tags=["receta"],
        default_unit_g=round(g, 1),
        unit_granularity=UnitGranularity.WHOLE,
        unit_name="porción",
    )


async def verify_recipe(
    *,
    recipe_id: UUID,
    recipe_repo: RecipeRepository,
    food_repo: FoodLookup,
) -> FoodItem:
    """Aprueba la receta y la materializa como alimento compuesto del tenant.

    `food_repo` debe estar en el tenant de la receta (no el del admin), para que
    el alimento compuesto quede disponible al entrenador dueño de la receta.
    """
    recipe = await recipe_repo.get(recipe_id)
    if recipe is None:
        raise ValidationError(f"Receta {recipe_id} no existe")
    if recipe.status == RecipeStatus.VERIFIED and recipe.compound_food_id:
        existing = await food_repo.get_by_ids([recipe.compound_food_id])
        if existing:
            return existing[0]

    food = _compound_food(recipe)
    await food_repo.add_custom(food)
    await recipe_repo.set_status(recipe_id, RecipeStatus.VERIFIED, compound_food_id=food.id)
    logger.info("recipe_verified", recipe_id=str(recipe_id), food_id=str(food.id))
    return food


async def reject_recipe(*, recipe_id: UUID, recipe_repo: RecipeRepository) -> None:
    recipe = await recipe_repo.get(recipe_id)
    if recipe is None:
        raise ValidationError(f"Receta {recipe_id} no existe")
    await recipe_repo.set_status(recipe_id, RecipeStatus.REJECTED)
    logger.info("recipe_rejected", recipe_id=str(recipe_id))
