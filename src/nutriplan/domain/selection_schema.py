"""Schema dinámico de selección por cliente (ADR-04).

El JSON Schema se construye en tiempo de ejecución inyectando el enum de
ids permitidos: el modelo NO PUEDE elegir un alimento fuera de la lista
ni inventar uno — Structured Outputs lo rechaza en el borde.

Con la IA usamos alias cortos (`f0`, `f1`…): los UUID hinchan el schema y
Gemini truncaba/rompía el JSON de la semana. El motor offline sigue con UUID.
"""

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, create_model

from nutriplan.domain.critique import food_aliases
from nutriplan.domain.models import FoodItem, MealSlot


def build_selection_schema(
    allowed: list[FoodItem],
    slots: Sequence[MealSlot] | None = None,
    *,
    use_aliases: bool = False,
) -> type[BaseModel]:
    """PlanSelection cuyo food_ids es Literal[<ids o alias permitidos>].

    `slots` son las comidas que come ESTE cliente: el día tiene tantas comidas como
    él coma, no siempre cinco.
    """
    if not allowed:
        raise ValueError("El conjunto permitido está vacío; no se puede generar")

    n_meals = len(slots) if slots else len(MealSlot)
    if use_aliases:
        ids = tuple(food_aliases(allowed))
    else:
        ids = tuple(sorted(str(f.id) for f in allowed))
    food_id_literal = Literal[ids]  # type: ignore[valid-type]

    meal_model = create_model(
        "MealSelectionStrict",
        __config__=ConfigDict(extra="forbid"),
        slot=(MealSlot, ...),
        food_ids=(list[food_id_literal], Field(min_length=1, max_length=4)),
        free_salad=(bool, False),
        dish_name=(str | None, Field(default=None, max_length=80)),
    )
    day_model = create_model(
        "DaySelectionStrict",
        __config__=ConfigDict(extra="forbid"),
        day_index=(int, Field(ge=0, le=6)),
        meals=(list[meal_model], Field(min_length=n_meals, max_length=n_meals)),  # type: ignore[valid-type]
    )
    return create_model(
        "PlanSelectionStrict",
        __config__=ConfigDict(extra="forbid"),
        days=(list[day_model], Field(min_length=7, max_length=7)),  # type: ignore[valid-type]
    )


def resolve_selection_aliases(raw: BaseModel, aliases: dict[str, UUID]) -> dict[str, object]:
    """Traduce f0… → UUID string para `PlanSelection`."""
    data = raw.model_dump()
    for day in data["days"]:
        for meal in day["meals"]:
            meal["food_ids"] = [str(aliases[a]) for a in meal["food_ids"]]
    return data
