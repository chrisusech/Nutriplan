"""Platos que ya salieron bien: la biblioteca devolviéndole algo al menú.

Guardar recetas solo sirve si vuelven a la generación. Un plato con nota alta,
hecho con alimentos que esta persona puede comer, es la mejor pista que se le
puede dar al modelo: no una idea inventada, sino algo que alguien ya se comió y
calificó bien.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from nutriplan.domain.dish_recipe import DishRecipe

# Una nota alta con dos votos ya es señal; con uno es la opinión de alguien.
MIN_RATING = 4.0
MIN_RATINGS = 2
MAX_IN_PROMPT = 12


@dataclass(frozen=True)
class ProvenDish:
    """Un plato con nota, reducido a lo que el prompt necesita."""

    name_es: str
    food_ids: tuple[UUID, ...]
    rating_avg: float
    rating_count: int


def proven_dishes(
    recipes: Iterable[DishRecipe],
    allowed_ids: set[UUID],
    *,
    limit: int = MAX_IN_PROMPT,
) -> list[ProvenDish]:
    """Los mejor valorados que esta persona SÍ puede comer.

    El filtro por catálogo permitido no es cosmético: proponerle al modelo un
    plato con un alimento vetado es pedirle que lo rompa, y el esquema de enum
    cerrado le impediría obedecer aunque quisiera.
    """
    picked = [
        ProvenDish(
            name_es=recipe.name_es.strip(),
            food_ids=tuple(recipe.food_ids),
            rating_avg=recipe.rating_avg or 0.0,
            rating_count=recipe.rating_count,
        )
        for recipe in recipes
        if recipe.name_es.strip()
        and recipe.food_ids
        and recipe.rating_count >= MIN_RATINGS
        and (recipe.rating_avg or 0.0) >= MIN_RATING
        and set(recipe.food_ids) <= allowed_ids
    ]
    picked.sort(key=lambda d: (-d.rating_avg, -d.rating_count, d.name_es))
    return picked[:limit]
