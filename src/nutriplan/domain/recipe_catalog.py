"""Catálogo curado de recetas: nombres y pasos humanos, macros del plan.

Los `foods` de cada entrada son tokens (`huevo_entero`, `arepa`) que se
resuelven contra los alimentos del plato. No fijan gramos.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from nutriplan.domain.food_matching import normalize
from nutriplan.domain.models import FoodItem, MealEntry


class CuratedRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name_es: str = Field(min_length=1, max_length=160)
    foods: list[str] = Field(min_length=1, max_length=8)
    steps: list[str] = Field(min_length=1, max_length=8)
    tips: str | None = Field(default=None, max_length=400)
    difficulty: str | None = None
    prep_minutes: int | None = Field(default=None, ge=1, le=180)


class RecipeCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "0.0.0"
    recipes: list[CuratedRecipe] = Field(default_factory=list)


def _token_needle(token: str) -> str:
    return normalize(token.replace("_", " ").strip())


def food_matches_token(food: FoodItem, token: str) -> bool:
    """¿Este alimento del plan es el que nombra el token del YAML?"""
    needle = _token_needle(token)
    if not needle:
        return False
    haystacks = [normalize(food.name_es), *(normalize(a) for a in food.aliases)]
    words = needle.split()
    for hay in haystacks:
        if not hay:
            continue
        if needle == hay or needle in hay:
            return True
        if words and all(w in hay for w in words):
            return True
    return False


def match_curated(
    meal: MealEntry,
    catalog: dict[UUID, FoodItem],
    recipes: Sequence[CuratedRecipe],
) -> CuratedRecipe | None:
    """La receta curada cuyos tokens cubren los alimentos del plato.

    Empate: gana la que nombra más alimentos (más específica).
    """
    meal_foods = [
        catalog[item.food_id]
        for item in meal.items
        if item.food_id and item.food_id in catalog and item.grams
    ]
    if not meal_foods:
        return None

    best: CuratedRecipe | None = None
    best_score = -1
    for recipe in recipes:
        if _covers(meal_foods, recipe.foods):
            score = len(recipe.foods)
            if score > best_score:
                best = recipe
                best_score = score
    return best


def _covers(meal_foods: list[FoodItem], tokens: list[str]) -> bool:
    """Cada token del YAML pega a un alimento distinto del plato."""
    if len(tokens) > len(meal_foods):
        return False
    used: set[int] = set()
    for token in tokens:
        found = False
        for i, food in enumerate(meal_foods):
            if i in used:
                continue
            if food_matches_token(food, token):
                used.add(i)
                found = True
                break
        if not found:
            return False
    return True


__all__ = [
    "CuratedRecipe",
    "RecipeCatalog",
    "food_matches_token",
    "match_curated",
]
