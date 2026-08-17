"""Cambiar un solo plato de la semana, sin gastar otra semana de membresía.

El motor completa proteína/carbo del slot si faltan; el solver recuadra el día.
Un mensaje libre no es el título del plato: eso lo pone el motor o la IA.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from nutriplan.domain.dish_recipe import dish_key
from nutriplan.domain.errors import GenerationError, ValidationError
from nutriplan.domain.generation_rules import (
    CARB_GROUP,
    PROTEIN_GROUP,
    SLOT_STRUCTURE,
    SlotStructure,
)
from nutriplan.domain.meal_template import Dish, _name_from_foods
from nutriplan.domain.models import (
    DayPlan,
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import solve_day_portions
from nutriplan.domain.restaurant import is_eating_out
from nutriplan.domain.swap_note import (
    asks_for_fries,
    closest_fries_or_potato,
    dish_from_foods,
    food_hits_needles,
    food_needles,
)
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


def complete_dish(
    slot: MealSlot,
    foods: Sequence[FoodItem],
    catalog: Sequence[FoodItem],
    *,
    note: str = "",
    name: str = "",
    free_salad: bool | None = None,
) -> Dish | None:
    """Si el slot exige proteína/carbo y el pick no los trae, se añaden del catálogo."""
    rule = SLOT_STRUCTURE[slot]
    unique = _dedupe(foods)
    if not unique:
        return None
    if rule.requires_protein and not any(f.category in PROTEIN_GROUP for f in unique):
        fill = _fill_role(catalog, PROTEIN_GROUP, slot, note)
        if fill is not None:
            unique = _dedupe([*unique, fill])
    if rule.requires_carb and not rule.carb_optional:
        if not any(f.category in CARB_GROUP for f in unique):
            fill = _fill_carb(catalog, slot, note)
            if fill is not None:
                unique = _dedupe([*unique, fill])
    unique = _trim(_role_order(unique), rule)
    if not unique:
        return None
    salad = rule.free_salad_default if free_salad is None else free_salad
    title = name.strip()[:80]
    if not title or title.lower() == note.strip().lower()[:80]:
        title = _name_from_foods(tuple(unique))
    dish = dish_from_foods(slot, unique, name=title)
    if dish is None:
        return None
    if salad == dish.free_salad:
        return dish
    return Dish(
        template_id=dish.template_id,
        name=dish.name,
        slot=dish.slot,
        foods=dish.foods,
        free_salad=salad,
    )


def _fill_carb(catalog: Sequence[FoodItem], slot: MealSlot, note: str) -> FoodItem | None:
    if asks_for_fries(note):
        closest = closest_fries_or_potato(list(catalog))
        if closest is not None:
            return closest
    return _fill_role(catalog, CARB_GROUP, slot, note)


def _fill_role(
    catalog: Sequence[FoodItem],
    group: set[FoodCategory],
    slot: MealSlot,
    note: str,
) -> FoodItem | None:
    needles = food_needles(note)
    candidates = [food for food in catalog if food.category in group and slot in food.meal_slots]
    if not candidates:
        candidates = [food for food in catalog if food.category in group]
    named = [food for food in candidates if needles and food_hits_needles(food, needles)]
    pool = named or candidates
    if not pool:
        return None
    return min(pool, key=lambda food: (not food.engine_default, food.kcal_100g, food.name_es))


def _trim(foods: list[FoodItem], rule: SlotStructure) -> list[FoodItem]:
    if len(foods) <= rule.max_items:
        return foods
    proteins = [food for food in foods if food.category in PROTEIN_GROUP]
    carbs = [food for food in foods if food.category in CARB_GROUP]
    rest = [food for food in foods if food not in proteins and food not in carbs]
    ordered: list[FoodItem] = []
    seen: set[UUID] = set()
    for food in (
        (proteins[:1] if rule.requires_protein else [])
        + (carbs[:1] if rule.requires_carb and not rule.carb_optional else [])
        + rest
        + proteins[1:]
        + carbs[1:]
    ):
        if food.id in seen or len(ordered) >= rule.max_items:
            continue
        seen.add(food.id)
        ordered.append(food)
    return ordered


def _role_order(foods: Sequence[FoodItem]) -> list[FoodItem]:
    """Proteína, carbo, el resto: el título de menú se lee como un plato."""
    proteins = [food for food in foods if food.category in PROTEIN_GROUP]
    carbs = [food for food in foods if food.category in CARB_GROUP]
    rest = [
        food
        for food in foods
        if food.category not in PROTEIN_GROUP and food.category not in CARB_GROUP
    ]
    return _dedupe([*proteins, *carbs, *rest])


def _dedupe(foods: Sequence[FoodItem]) -> list[FoodItem]:
    seen: set[UUID] = set()
    out: list[FoodItem] = []
    for food in foods:
        if food.id in seen:
            continue
        seen.add(food.id)
        out.append(food)
    return out
