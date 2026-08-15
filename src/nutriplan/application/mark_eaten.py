"""Marcar (o desmarcar) una comida como ya comida. No reporciona nada."""

from __future__ import annotations

from nutriplan.domain.errors import ValidationError
from nutriplan.domain.models import DayPlan, MealSlot


def mark_eaten(day: DayPlan, slot: MealSlot, eaten: bool) -> DayPlan:
    """Cambia solo la bandera. Las kcal del plato siguen siendo las del menú."""
    if slot not in {m.slot for m in day.meals}:
        raise ValidationError("Esa comida no está en tu día.")
    meals = [m.model_copy(update={"eaten": eaten}) if m.slot is slot else m for m in day.meals]
    return day.model_copy(update={"meals": meals})
