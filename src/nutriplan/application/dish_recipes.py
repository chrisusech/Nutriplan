"""Conseguir la receta de un plato: caché, luego catálogo, y solo entonces IA.

El orden importa. Con cientos de personas sobre trece plantillas, casi todos los
platos se repiten: pedirle al modelo lo que ya está escrito quemaría la cuota
gratis en una tarde.
"""

from pathlib import Path
from typing import Protocol
from uuid import UUID

import structlog

from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.domain.dish_recipe import DishRecipe, GeneratedRecipe, dish_key
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import FoodItem, MealEntry
from nutriplan.ports.llm_client import LLMClient

logger = structlog.get_logger(__name__)

RECIPE_PROMPT_VERSION = 1


class DishRecipeRepository(Protocol):
    async def get_many(self, keys: list[str]) -> dict[str, DishRecipe]: ...
    async def add(self, recipe: DishRecipe, *, model: str, prompt_version: str) -> None: ...


def ingredient_lines(meal: MealEntry, catalog: dict[UUID, FoodItem]) -> list[str]:
    """Los ingredientes tal como se leen en la app, con sus gramos.

    Esto NO se le pide a la IA: son los números del plan, y los números son del
    código. La IA solo escribe los pasos.
    """
    from nutriplan.ui.web.format import portion_text

    lines = [
        portion_text(item.grams, catalog[item.food_id])
        for item in meal.items
        if item.food_id and item.grams and item.food_id in catalog
    ]
    if meal.free_salad:
        lines.append("Ensalada libre")
    if meal.free_protein:
        lines.append("Proteína libre")
    return lines


def _prompt_for(meal: MealEntry, catalog: dict[UUID, FoodItem]) -> str:
    foods = ", ".join(
        catalog[i.food_id].name_es for i in meal.items if i.food_id and i.food_id in catalog
    )
    return (
        f"Plato: {meal.dish_name or 'sin nombre'}\n"
        f"Comida: {meal.slot.value}\n"
        f"Ingredientes: {foods}\n"
        "Explica cómo se prepara."
    )


async def recipes_for_week(
    *,
    meals: list[MealEntry],
    catalog: dict[UUID, FoodItem],
    repo: DishRecipeRepository,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
    static: dict[str, list[str]] | None = None,
) -> dict[str, DishRecipe]:
    """Las recetas de todos los platos distintos de la semana.

    Se resuelve por plato único, no por comida: siete cenas del mismo plato son
    una sola receta y una sola llamada.
    """
    wanted = {m.dish_key: m for m in meals if m.dish_key and not m.is_free_meal}
    if not wanted:
        return {}

    found = await repo.get_many(list(wanted))
    missing = {k: m for k, m in wanted.items() if k not in found}
    if not missing:
        logger.info("dish_recipes_all_cached", dishes=len(found))
        return found

    prompt = load_prompt(prompts_dir, "dish_recipe", RECIPE_PROMPT_VERSION)
    version = f"dish_recipe.v{RECIPE_PROMPT_VERSION}"

    for key, meal in missing.items():
        recipe = _from_static(key, meal, catalog, static)
        if recipe is None and llm is not None:
            recipe = await _from_llm(key, meal, catalog, llm, prompt.text, model)
        if recipe is None:
            continue
        found[key] = recipe
        await repo.add(recipe, model=model if recipe.source == "ai" else "", prompt_version=version)

    logger.info("dish_recipes_resolved", total=len(found), generated=len(missing))
    return found


def _from_static(
    key: str,
    meal: MealEntry,
    catalog: dict[UUID, FoodItem],
    static: dict[str, list[str]] | None,
) -> DishRecipe | None:
    """La receta escrita a mano en `meal_templates.yaml`, si la plantilla la trae."""
    steps = (static or {}).get(meal.template_id or "")
    if not steps:
        return None
    return DishRecipe(
        dish_key=key,
        template_id=meal.template_id,
        name_es=meal.dish_name or "",
        ingredients=ingredient_lines(meal, catalog),
        steps=list(steps),
        source="yaml",
    )


async def _from_llm(
    key: str,
    meal: MealEntry,
    catalog: dict[UUID, FoodItem],
    llm: LLMClient,
    system: str,
    model: str,
) -> DishRecipe | None:
    try:
        generated = await llm.extract(
            system=system, text=_prompt_for(meal, catalog),
            schema=GeneratedRecipe, model=model,
        )
    except LLMError as exc:
        # Sin receta, la app muestra los ingredientes y ya. No es un fallo.
        logger.warning("dish_recipe_skipped", dish=meal.dish_name, error=str(exc))
        return None
    return DishRecipe(
        dish_key=key,
        template_id=meal.template_id,
        name_es=meal.dish_name or "",
        ingredients=ingredient_lines(meal, catalog),
        steps=generated.steps,
        prep_minutes=generated.prep_minutes,
        difficulty=generated.difficulty,
        tips=generated.tips or None,
        source="ai",
    )


__all__ = ["DishRecipeRepository", "dish_key", "ingredient_lines", "recipes_for_week"]
