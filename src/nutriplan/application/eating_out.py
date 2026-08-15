"""Sustituir un slot por un plato de calle y recuadrar el resto del día.

Los macros del restaurante son fijos (vienen del catálogo). El solver reporciona
desayuno/almuerzo/snacks contra lo que queda del presupuesto. Si la pizza ya se
comió la grasa, los otros slots bajan al piso y se avisa con la verdad.
"""

from __future__ import annotations

from uuid import UUID

from nutriplan.domain.calculation import energy_kcal
from nutriplan.domain.dish_recipe import dish_key
from nutriplan.domain.errors import GenerationError, ValidationError
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
from nutriplan.domain.restaurant import (
    RestaurantCatalog,
    RestaurantDish,
    is_eating_out,
    out_template_id,
)
from nutriplan.domain.validation import day_totals

_SLOT_ORDER = list(MealSlot)


def sum_macros(meals: list[MealEntry]) -> MacroTargets:
    """Suma P/C/G de varios platos. Las kcal salen de esa suma, no de redondeos."""
    p = sum(m.computed.protein_g for m in meals)
    c = sum(m.computed.carb_g for m in meals)
    g = sum(m.computed.fat_g for m in meals)
    if p + c + g <= 0:
        return MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0)
    return MacroTargets(
        kcal=energy_kcal(p, c, g),
        protein_g=round(p, 1),
        carb_g=round(c, 1),
        fat_g=round(g, 1),
        fiber_g=round(sum(m.computed.fiber_g for m in meals), 1),
    )


def is_locked(meal: MealEntry) -> bool:
    """Un plato que el solver no puede tocar: ya comido, de calle, o vacío."""
    return meal.eaten or is_eating_out(meal) or not meal.items


def subtract_macros(daily: MacroTargets, eaten: MacroTargets) -> MacroTargets:
    """Lo que queda del día después de la calle. Nunca negativo."""
    p = max(0.0, daily.protein_g - eaten.protein_g)
    c = max(0.0, daily.carb_g - eaten.carb_g)
    g = max(0.0, daily.fat_g - eaten.fat_g)
    return MacroTargets(
        kcal=energy_kcal(p, c, g) if (p + c + g) > 0 else 0.0,
        protein_g=round(p, 1),
        carb_g=round(c, 1),
        fat_g=round(g, 1),
        fiber_g=max(0.0, daily.fiber_g - eaten.fiber_g),
    )


def overflow_of(daily: MacroTargets, actual: MacroTargets) -> MacroTargets:
    return MacroTargets(
        kcal=round(max(0.0, actual.kcal - daily.kcal), 1),
        protein_g=round(max(0.0, actual.protein_g - daily.protein_g), 1),
        carb_g=round(max(0.0, actual.carb_g - daily.carb_g), 1),
        fat_g=round(max(0.0, actual.fat_g - daily.fat_g), 1),
    )


def _overflow_warning(over: MacroTargets) -> str | None:
    if over.kcal < 8 and over.fat_g < 2:
        return None
    bits: list[str] = []
    if over.kcal >= 8:
        bits.append(f"~{over.kcal:.0f} kcal")
    if over.fat_g >= 2:
        bits.append(f"{over.fat_g:.0f} g de grasa")
    if over.protein_g >= 8:
        bits.append(f"{over.protein_g:.0f} g de proteína")
    return "Con esto te pasas " + " / ".join(bits) + "."


def _gap_warning(daily: MacroTargets, actual: MacroTargets) -> str | None:
    """La verdad del día: te pasas, o te quedas corto, o cuadra."""
    over = _overflow_warning(overflow_of(daily, actual))
    if over:
        return over
    short = daily.kcal - actual.kcal
    if short < 80:
        return None
    return (
        f"Con este plato el día queda en {actual.kcal:.0f} kcal "
        f"(faltan {short:.0f}). Lo ya comido no se cambia."
    )


def make_eating_out_entry(
    *,
    slot: MealSlot,
    restaurant_id: str,
    restaurant_name: str,
    dish: RestaurantDish,
    servings: float = 1.0,
) -> MealEntry:
    n = max(servings, 0.5)
    macros = dish.macros(n)
    template_id = out_template_id(restaurant_id, dish.id)
    qty = dish.portion if n == 1 else f"{n:g} × {dish.portion}"
    name = f"{restaurant_name} · {dish.name}"
    return MealEntry(
        slot=slot,
        template_id=template_id,
        dish_name=f"{name} ({qty})",
        dish_key=dish_key(template_id, []),
        items=[],
        computed=macros,
        is_free_meal=False,
    )


def apply_eating_out(
    day: DayPlan,
    *,
    slot: MealSlot,
    restaurant_id: str,
    restaurant_name: str,
    dish: RestaurantDish,
    foods_by_id: dict[UUID, FoodItem],
    daily: MacroTargets,
    config: NutritionConfig,
    servings: float = 1.0,
) -> tuple[DayPlan, str | None]:
    """Sustituye el slot, reporciona el resto, avisa si se pasa del día."""
    if slot not in {m.slot for m in day.meals}:
        raise ValidationError("Esa comida no está en tu día.")
    out = make_eating_out_entry(
        slot=slot,
        restaurant_id=restaurant_id,
        restaurant_name=restaurant_name,
        dish=dish,
        servings=servings,
    )
    others = [m for m in day.meals if m.slot is not slot]
    locked = [m for m in others if is_locked(m)]
    flexible = [m for m in others if m not in locked]
    remaining = subtract_macros(daily, out.computed)
    if locked:
        remaining = subtract_macros(remaining, sum_macros(locked))
    rebuilt = _reportion(flexible, remaining, foods_by_id, config)
    meals = sorted([*locked, *rebuilt, out], key=lambda m: _SLOT_ORDER.index(m.slot))
    totals = day_totals(meals)
    warning = _gap_warning(daily, totals)
    return DayPlan(day_index=day.day_index, meals=meals, totals=totals), warning


def _reportion(
    meals: list[MealEntry],
    remaining: MacroTargets,
    foods_by_id: dict[UUID, FoodItem],
    config: NutritionConfig,
) -> list[MealEntry]:
    cookable = [m for m in meals if m.items and not is_eating_out(m)]
    kept = [m for m in meals if m not in cookable]
    if not cookable or remaining.kcal <= 0:
        return list(meals)
    inputs = [
        (
            m.slot,
            [foods_by_id[i.food_id] for i in m.items if i.food_id and i.food_id in foods_by_id],
        )
        for m in cookable
    ]
    inputs = [(slot, foods) for slot, foods in inputs if foods]
    if not inputs:
        return list(meals)
    try:
        solved = solve_day_portions(inputs, remaining, config)
    except GenerationError:
        return list(meals)
    by_slot = {m.slot: m for m in cookable}
    out: list[MealEntry] = []
    for solved_meal in solved:
        original = by_slot[solved_meal.slot]
        food_ids = [p.food_id for p in solved_meal.portions]
        out.append(
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
    return out + kept


def resolve_dish(
    catalog: RestaurantCatalog, restaurant_id: str, dish_id: str
) -> tuple[str, RestaurantDish]:
    found = catalog.find(restaurant_id, dish_id)
    if found is None:
        raise ValidationError("Ese plato no está en el catálogo.")
    resto, dish = found
    return resto.name, dish
