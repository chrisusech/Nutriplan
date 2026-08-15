"""Cambiar un solo plato de la semana, sin gastar otra semana de membresía.

Los candidatos salen del motor (el mismo pool con el que se armó el menú). El
solver recuadra el día. Un mensaje libre sesga el ranking; no inventa alimentos.
"""

from __future__ import annotations

from uuid import UUID

from nutriplan.domain.dish_recipe import dish_key
from nutriplan.domain.errors import GenerationError, ValidationError
from nutriplan.domain.meal_template import Dish
from nutriplan.domain.models import (
    DayPlan,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import solve_day_portions
from nutriplan.domain.restaurant import is_eating_out
from nutriplan.domain.validation import day_totals

_SLOT_ORDER = list(MealSlot)


def swap_slot(
    day: DayPlan,
    *,
    slot: MealSlot,
    pick: Dish,
    foods_by_id: dict[UUID, FoodItem],
    daily: MacroTargets,
    config: NutritionConfig,
    wish: str = "",
) -> DayPlan:
    """Sustituye el plato del slot por el candidato elegido.

    `wish` ya no nombra el plato: el título es el del motor o de la IA. El
    texto libre se usaba tal cual y «quisiera comer pollo sudado» acababa
    en la tarjeta.
    """
    _ = wish
    current = next((m for m in day.meals if m.slot is slot), None)
    if current is None:
        raise ValidationError("Esa comida no está en tu día.")
    if is_eating_out(current) or current.is_free_meal:
        raise ValidationError(
            "Ese plato es de calle: elige otro restaurante, no un cambio de casa."
        )
    replacement = MealEntry(
        slot=slot,
        template_id=pick.template_id,
        dish_name=pick.name[:80],
        dish_key=dish_key(pick.template_id, list(pick.food_ids)),
        items=[
            MealItem(food_id=fid, grams=100.0, position=i) for i, fid in enumerate(pick.food_ids)
        ],
        computed=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
        free_salad=pick.free_salad,
    )
    others = [m for m in day.meals if m.slot is not slot]
    draft = sorted([*others, replacement], key=lambda m: _SLOT_ORDER.index(m.slot))
    return _solve_day(draft, foods_by_id, daily, config, day.day_index)


def _solve_day(
    meals: list[MealEntry],
    foods_by_id: dict[UUID, FoodItem],
    daily: MacroTargets,
    config: NutritionConfig,
    day_index: int,
) -> DayPlan:
    from nutriplan.application.eating_out import is_locked, subtract_macros, sum_macros

    locked = [m for m in meals if is_locked(m)]
    cookable = [m for m in meals if m not in locked]
    inputs = [
        (
            m.slot,
            [foods_by_id[i.food_id] for i in m.items if i.food_id and i.food_id in foods_by_id],
        )
        for m in cookable
    ]
    inputs = [(s, foods) for s, foods in inputs if foods]
    if not inputs:
        raise GenerationError("No quedó nada que porcionar en ese día.")
    remaining = subtract_macros(daily, sum_macros(locked)) if locked else daily
    solved = solve_day_portions(inputs, remaining, config)
    by_slot = {m.slot: m for m in cookable}
    rebuilt: list[MealEntry] = list(locked)
    for solved_meal in solved:
        original = by_slot[solved_meal.slot]
        food_ids = [p.food_id for p in solved_meal.portions]
        rebuilt.append(
            original.model_copy(
                update={
                    "items": [
                        MealItem(food_id=p.food_id, grams=p.grams, position=i)
                        for i, p in enumerate(solved_meal.portions)
                    ],
                    "computed": solved_meal.computed,
                    "dish_key": dish_key(original.template_id, food_ids) if food_ids else None,
                }
            )
        )
    rebuilt.sort(key=lambda m: _SLOT_ORDER.index(m.slot))
    return DayPlan(day_index=day_index, meals=rebuilt, totals=day_totals(rebuilt))
