"""Schema dinámico de selección por cliente (ADR-04).

El JSON Schema se construye en tiempo de ejecución inyectando el enum de
food_ids permitidos: el modelo NO PUEDE elegir un alimento fuera de la lista
ni inventar uno — Structured Outputs lo rechaza en el borde.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from nutriplan.domain.models import FoodItem, MealSlot


def build_selection_schema(allowed: list[FoodItem]) -> type[BaseModel]:
    """PlanSelection cuyo food_ids es Literal[<ids permitidos>]."""
    if not allowed:
        raise ValueError("El conjunto permitido está vacío; no se puede generar")

    ids = tuple(sorted(str(f.id) for f in allowed))
    food_id_literal = Literal[ids]  # type: ignore[valid-type]

    meal_model = create_model(
        "MealSelectionStrict",
        __config__=ConfigDict(extra="forbid"),
        slot=(MealSlot, ...),
        food_ids=(list[food_id_literal], Field(min_length=1, max_length=4)),
        free_salad=(bool, False),
    )
    day_model = create_model(
        "DaySelectionStrict",
        __config__=ConfigDict(extra="forbid"),
        day_index=(int, Field(ge=0, le=6)),
        meals=(list[meal_model], Field(min_length=5, max_length=5)),
    )
    return create_model(
        "PlanSelectionStrict",
        __config__=ConfigDict(extra="forbid"),
        days=(list[day_model], Field(min_length=7, max_length=7)),
    )
