"""View-model de "Mi semana": el menú tal como lo lee la persona.

Sin lógica de negocio. Los números vienen del dominio; aquí solo se eligen
palabras, iconos y el orden en que se leen.
"""

from datetime import date
from typing import Any
from uuid import UUID

from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.models import DayPlan, FoodItem, MealEntry, MealSlot, PlanCycle
from nutriplan.ui.web.format import DAY_LABELS, DAY_SHORT, portion_text

SLOT_META: dict[MealSlot, dict[str, str]] = {
    MealSlot.BREAKFAST: {"name": "Desayuno", "time": "7:00 am", "icon": "wb_sunny"},
    MealSlot.SNACK_AM: {"name": "Snack de media mañana", "time": "10:30 am", "icon": "nutrition"},
    MealSlot.LUNCH: {"name": "Almuerzo", "time": "1:00 pm", "icon": "restaurant"},
    MealSlot.SNACK_PM: {"name": "Snack de la tarde", "time": "4:30 pm", "icon": "nutrition"},
    MealSlot.DINNER: {"name": "Cena", "time": "7:30 pm", "icon": "dinner_dining"},
}

_SLOT_ORDER = list(MealSlot)


def day_chips(cycle: PlanCycle, selected: int, today: int | None = None) -> list[dict[str, Any]]:
    """Los siete días. `today` se resalta para no tener que buscarlo."""
    if today is None:
        today = date.today().weekday()
    return [
        {
            "index": day.day_index,
            "short": DAY_SHORT[day.day_index],
            "label": DAY_LABELS[day.day_index],
            "on": day.day_index == selected,
            "is_today": day.day_index == today,
        }
        for day in sorted(cycle.days, key=lambda d: d.day_index)
    ]


def _meal_title(meal: MealEntry, foods: dict[UUID, FoodItem]) -> str:
    """El nombre del plato si lo tiene; si no, sus alimentos.

    El nombre lo pone el motor de platos o el crítico. Cuando falta —modo
    offline sin catálogo— la lista sigue siendo mejor que un hueco.
    """
    if meal.dish_name:
        return meal.dish_name
    names = [
        foods[i.food_id].name_es for i in meal.items if i.food_id and i.food_id in foods
    ]
    if not names:
        return SLOT_META[meal.slot]["name"]
    return " con ".join([names[0].capitalize(), *names[1:]])


def meal_view(
    meal: MealEntry,
    foods: dict[UUID, FoodItem],
    recipe: DishRecipe | None = None,
    rating: int | None = None,
) -> dict[str, Any]:
    meta = SLOT_META[meal.slot]
    if meal.is_free_meal:
        return {
            "slot": meal.slot.value,
            "slot_label": meta["name"],
            "time": meta["time"],
            "icon": "celebration",
            "title": "Comida libre",
            "is_free": True,
            "kcal": 0,
            "ingredients": [],
            "steps": [],
            "rating": rating,
        }

    ingredients = [
        portion_text(item.grams, foods[item.food_id])
        for item in meal.items
        if item.food_id and item.grams and item.food_id in foods
    ]
    if meal.free_salad:
        ingredients.append("Ensalada libre")
    if meal.free_protein:
        ingredients.append("Proteína libre")

    return {
        "slot": meal.slot.value,
        "slot_label": meta["name"],
        "time": meta["time"],
        "icon": meta["icon"],
        "title": _meal_title(meal, foods),
        "is_free": False,
        "kcal": round(meal.computed.kcal),
        "ingredients": ingredients,
        "steps": list(recipe.steps) if recipe else [],
        "prep_minutes": recipe.prep_minutes if recipe else None,
        "difficulty": recipe.difficulty if recipe else None,
        "tips": recipe.tips if recipe else None,
        "rating": rating,
    }


def day_view(
    day: DayPlan,
    foods: dict[UUID, FoodItem],
    recipes: dict[str, DishRecipe] | None = None,
    ratings: dict[str, int] | None = None,
) -> dict[str, Any]:
    recipes = recipes or {}
    ratings = ratings or {}
    meals = sorted(day.meals, key=lambda m: _SLOT_ORDER.index(m.slot))
    return {
        "index": day.day_index,
        "label": DAY_LABELS[day.day_index],
        "kcal": round(day.totals.kcal),
        "protein_g": round(day.totals.protein_g),
        "carb_g": round(day.totals.carb_g),
        "fat_g": round(day.totals.fat_g),
        "meals": [
            meal_view(
                m,
                foods,
                recipes.get(m.dish_key or ""),
                ratings.get(m.slot.value),
            )
            for m in meals
        ],
    }
