"""Conseguir la receta de un plato: caché, luego catálogo, y solo entonces IA.

El orden importa. Con cientos de personas sobre trece plantillas, casi todos los
platos se repiten: pedirle al modelo lo que ya está escrito quemaría la cuota
gratis en una tarde.
"""

from pathlib import Path
from typing import Protocol
from uuid import UUID

import structlog

from nutriplan.application.prompts import load_prompt
from nutriplan.domain.dish_recipe import DishRecipe, GeneratedRecipe, dish_key
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import FoodItem, MacroTargets, MealEntry
from nutriplan.domain.portion_label import portion_text
from nutriplan.domain.portioning import macros_of
from nutriplan.domain.recipe_catalog import CuratedRecipe, match_curated
from nutriplan.ports.llm_client import LLMClient

logger = structlog.get_logger(__name__)

RECIPE_PROMPT_VERSION = 4

# Cuándo suele prepararse cada comida — contexto para la IA, no cifra de kcal.
_SLOT_HINT = {
    "desayuno": "por la mañana, rápido si va con prisa",
    "snack_am": "media mañana, snack corto",
    "almuerzo": "al mediodía, comida principal",
    "snack_pm": "media tarde, snack corto",
    "cena": "por la noche, sin complicarse",
}


class DishRecipeRepository(Protocol):
    async def get_many(self, keys: list[str]) -> dict[str, DishRecipe]: ...
    async def add(self, recipe: DishRecipe, *, model: str, prompt_version: str) -> None: ...
    async def top_rated(
        self, *, min_rating: float, min_count: int, limit: int = 200
    ) -> list[DishRecipe]: ...


def ingredient_lines(meal: MealEntry, catalog: dict[UUID, FoodItem]) -> list[str]:
    """Los ingredientes tal como se leen en la app, con sus gramos.

    Esto NO se le pide a la IA: son los números del plan, y los números son del
    código. La IA solo escribe los pasos.
    """
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


def reference_portion(
    meal: MealEntry, catalog: dict[UUID, FoodItem]
) -> tuple[list[UUID], dict[str, float], MacroTargets | None]:
    """De qué está hecho el plato y cuánto alimenta, tal como se sirvió.

    Es la porción con la que la receta se estrenó, no una promesa: quien la lea
    con otras kcal verá otros gramos en su plan. Guardarla igual sirve para
    ordenar la biblioteca y para que el catálogo curado nazca con cifras.

    Las calcula el código; el modelo no escribe números en ningún camino.
    """
    portions = [
        (catalog[item.food_id], float(item.grams))
        for item in meal.items
        if item.food_id and item.grams and item.food_id in catalog
    ]
    if not portions:
        return [], {}, None
    return (
        sorted((f.id for f, _ in portions), key=str),
        {str(f.id): g for f, g in portions},
        macros_of(portions),
    )


def _prompt_for(meal: MealEntry, catalog: dict[UUID, FoodItem]) -> str:
    """Contexto rico para el modelo: porciones solo de referencia, no a copiar."""
    lines = ingredient_lines(meal, catalog)
    slot = meal.slot.value
    hint = _SLOT_HINT.get(slot, slot)
    ingredients = "\n".join(f"- {line}" for line in lines) or "- (sin ingredientes)"
    return (
        f"Plato tentativo: {meal.dish_name or 'sin nombre'}\n"
        f"Comida: {slot} ({hint})\n"
        f"Ingredientes del plan (NO repitas gramos ni cifras en los pasos):\n"
        f"{ingredients}\n"
        "Si el nombre tentativo es cómo lo pidió la persona, úsalo de base para "
        "name_es (técnica y sabor) siempre que los ingredientes lo permitan. "
        "No inventes un alimento que no esté en la lista.\n"
        "Primero juzga si el plato es adecuado para esa comida; "
        "si pasa, inventa un name_es culinario con técnica/sabor y explica "
        "cómo se prepara en orden real de cocción (cocina latina)."
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
    curated: list[CuratedRecipe] | None = None,
    prefer_ai: bool = False,
) -> dict[str, DishRecipe]:
    """Las recetas de todos los platos distintos.

    Orden normal: caché → catálogo curado → IA → plantilla YAML.
    Con `prefer_ai` (al mirar un día): la IA escribe/mejora lo que solo tenía
    plantilla `yaml` en caché; AI y curated se reutilizan.
    """
    wanted = {m.dish_key: m for m in meals if m.dish_key and not m.is_free_meal}
    if not wanted:
        return {}

    cached = await repo.get_many(list(wanted))
    if prefer_ai and llm is not None:
        found = {k: r for k, r in cached.items() if r.source in ("ai", "curated")}
    else:
        found = dict(cached)
    missing = {k: m for k, m in wanted.items() if k not in found}
    if not missing:
        logger.info("dish_recipes_all_cached", dishes=len(found), prefer_ai=prefer_ai)
        return found

    prompt = load_prompt(prompts_dir, "dish_recipe", RECIPE_PROMPT_VERSION)
    version = f"dish_recipe.v{RECIPE_PROMPT_VERSION}"
    curated_list = curated or []

    for key, meal in missing.items():
        recipe: DishRecipe | None = None
        if prefer_ai and llm is not None:
            recipe = await _from_llm(key, meal, catalog, llm, prompt.text, model)
            if recipe is None:
                recipe = _from_curated(key, meal, catalog, curated_list)
            if recipe is None:
                recipe = cached.get(key)  # conserva yaml previo si la IA falló
            if recipe is None:
                recipe = _from_static(key, meal, catalog, static)
        else:
            recipe = _from_curated(key, meal, catalog, curated_list)
            if recipe is None and llm is not None:
                recipe = await _from_llm(key, meal, catalog, llm, prompt.text, model)
            if recipe is None:
                recipe = _from_static(key, meal, catalog, static)
        if recipe is None:
            continue
        food_ids, grams, macros = reference_portion(meal, catalog)
        recipe = recipe.model_copy(
            update={
                "food_ids": food_ids,
                "reference_grams": grams,
                "macros": macros,
            }
        )
        found[key] = recipe
        await repo.add(
            recipe,
            model=model if recipe.source == "ai" else "",
            prompt_version=version,
        )

    logger.info(
        "dish_recipes_resolved",
        total=len(found),
        generated=len(missing),
        prefer_ai=prefer_ai,
    )
    return found


def _from_curated(
    key: str,
    meal: MealEntry,
    catalog: dict[UUID, FoodItem],
    curated: list[CuratedRecipe],
) -> DishRecipe | None:
    hit = match_curated(meal, catalog, curated)
    if hit is None:
        return None
    return DishRecipe(
        dish_key=key,
        template_id=meal.template_id,
        name_es=hit.name_es,
        ingredients=ingredient_lines(meal, catalog),
        steps=list(hit.steps),
        prep_minutes=hit.prep_minutes,
        difficulty=hit.difficulty,
        tips=hit.tips,
        source="curated",
    )


def _from_static(
    key: str,
    meal: MealEntry,
    catalog: dict[UUID, FoodItem],
    static: dict[str, list[str]] | None,
) -> DishRecipe | None:
    """La receta escrita a mano en `meal_templates.yaml`, si la plantilla la trae.

    No se usa si el plato quedó incompleto frente a lo que el YAML asume
    (fruta sola con pasos de crema): mentiría en la app.
    """
    steps = (static or {}).get(meal.template_id or "")
    if not steps:
        return None
    lines = ingredient_lines(meal, catalog)
    blob = " ".join(lines).lower()
    for step in steps:
        lower = step.lower()
        if ("crema" in lower or "frutos secos" in lower) and not any(
            t in blob
            for t in (
                "mantequilla",
                "almendra",
                "maní",
                "mani",
                "marañón",
                "maranon",
                "tahini",
                "nuez",
                "crema",
                "frutos secos",
            )
        ):
            return None
    return DishRecipe(
        dish_key=key,
        template_id=meal.template_id,
        name_es=meal.dish_name or "",
        ingredients=lines,
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
            system=system,
            text=_prompt_for(meal, catalog),
            schema=GeneratedRecipe,
            model=model,
        )
    except LLMError as exc:
        # Sin receta, la app muestra los ingredientes y ya. No es un fallo.
        logger.warning("dish_recipe_skipped", dish=meal.dish_name, error=str(exc))
        return None

    if generated.adequacy == "reject":
        # Soft: el plato se queda en el menú; no cacheamos una receta de algo
        # que el crítico acaba de tumbar como combo/slot.
        logger.info(
            "dish_recipe_rejected_by_critic",
            dish=meal.dish_name,
            slot=meal.slot.value,
            codes=list(generated.issue_codes),
            reason=(generated.critique_reason or "")[:120],
        )
        return None

    tips = generated.tips or ""
    if generated.adequacy == "warn" and generated.critique_reason:
        note = generated.critique_reason.strip()
        tips = f"{tips} {note}".strip() if tips else note

    culinary = (generated.name_es or "").strip()
    return DishRecipe(
        dish_key=key,
        template_id=meal.template_id,
        name_es=culinary or meal.dish_name or "",
        ingredients=ingredient_lines(meal, catalog),
        steps=generated.steps,
        prep_minutes=generated.prep_minutes,
        difficulty=generated.difficulty,
        tips=tips or None,
        source="ai",
    )


__all__ = ["DishRecipeRepository", "dish_key", "ingredient_lines", "recipes_for_week"]
