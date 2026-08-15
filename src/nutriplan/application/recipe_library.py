"""Convertir una receta que salió bien en una receta curada.

`dish_recipes` es la caché que se llena sola; el YAML es lo que un humano ya
revisó. Promover es el puente entre las dos y va en una sola dirección: lo que
la gente calificó bien deja de depender de que la IA vuelva a acertar.
"""

from __future__ import annotations

from uuid import UUID

from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.errors import ValidationError
from nutriplan.domain.food_matching import normalize
from nutriplan.domain.models import FoodItem
from nutriplan.domain.recipe_catalog import CuratedRecipe

# Debajo de esto una receta no se promueve: sin votos suficientes, una nota
# alta es la opinión de una persona, no la calidad del plato.
MIN_RATINGS_TO_PROMOTE = 2
MIN_RATING_TO_PROMOTE = 4.0


def food_token(food: FoodItem) -> str:
    """El token con el que el YAML nombra un alimento (`pechuga_de_pollo`)."""
    return normalize(food.name_es).replace(" ", "_")


def curated_from(recipe: DishRecipe, foods: dict[UUID, FoodItem]) -> CuratedRecipe:
    """La entrada de catálogo equivalente a una receta ya generada."""
    tokens = [food_token(foods[f]) for f in recipe.food_ids if f in foods]
    if not tokens or not recipe.steps:
        raise ValidationError(
            "Esa receta todavía no sabe de qué está hecha: se completa la "
            "próxima vez que alguien se coma el plato."
        )
    return CuratedRecipe(
        id=f"{recipe.template_id or 'plato'}__{recipe.dish_key[:8]}",
        name_es=recipe.name_es,
        foods=tokens[:8],
        steps=list(recipe.steps)[:8],
        tips=recipe.tips,
        difficulty=recipe.difficulty,
        prep_minutes=recipe.prep_minutes,
    )


def is_promotable(recipe: DishRecipe) -> bool:
    """Si ya se ganó el sitio en el catálogo humano."""
    return (
        recipe.source == "ai"
        and recipe.rating_count >= MIN_RATINGS_TO_PROMOTE
        and (recipe.rating_avg or 0.0) >= MIN_RATING_TO_PROMOTE
    )
