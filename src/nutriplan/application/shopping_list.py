"""Lista de compra de la semana: gramos del plan, agregados por alimento.

El código es dueño de las cantidades. Aquí no hay IA: se suman los `MealItem`
del `PlanCycle` y se agrupan por categoría para la compra del súper.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from nutriplan.domain.models import FoodCategory, FoodItem, PlanCycle, UnitGranularity
from nutriplan.domain.restaurant import is_eating_out

# Orden de pasillo estable (no alfabético de enum).
_CATEGORY_ORDER: tuple[FoodCategory, ...] = (
    FoodCategory.PROTEIN,
    FoodCategory.DAIRY,
    FoodCategory.CARB,
    FoodCategory.FRUIT,
    FoodCategory.VEGETABLE,
    FoodCategory.FAT,
    FoodCategory.OTHER,
)

_CATEGORY_LABEL: dict[FoodCategory, str] = {
    FoodCategory.PROTEIN: "Proteínas",
    FoodCategory.DAIRY: "Lácteos",
    FoodCategory.CARB: "Carbohidratos",
    FoodCategory.FRUIT: "Frutas",
    FoodCategory.VEGETABLE: "Verduras",
    FoodCategory.FAT: "Grasas y aceites",
    FoodCategory.OTHER: "Otros",
}


@dataclass(frozen=True)
class ShoppingLine:
    food_id: UUID
    name_es: str
    category: FoodCategory
    grams: float
    unit_label: str | None  # p. ej. "14 huevos" si es contable
    # Lo marcó como "ya lo tengo en casa" antes de generar. La línea NO se borra:
    # hay que poder ver que el arroz de la semana sale de tu bolsa, no que
    # desapareció del menú.
    ya_tengo: bool = False


@dataclass(frozen=True)
class ShoppingGroup:
    category: FoodCategory
    label: str
    lines: tuple[ShoppingLine, ...]


def shopping_list_for_plan(
    plan: PlanCycle,
    catalog: dict[UUID, FoodItem],
    *,
    en_casa: set[UUID] | frozenset[UUID] = frozenset(),
) -> list[ShoppingGroup]:
    """Suma gramos de toda la semana, excluye libres y comidas libres.

    `en_casa` son los alimentos que la persona dijo tener antes de generar: se
    marcan y bajan al final de su grupo, pero siguen en la lista con sus gramos.
    """
    totals: dict[UUID, float] = {}
    for day in plan.days:
        for meal in day.meals:
            if meal.is_free_meal or is_eating_out(meal):
                continue
            for item in meal.items:
                if not item.food_id or not item.grams:
                    continue
                food = catalog.get(item.food_id)
                if food is None or food.is_free:
                    continue
                totals[item.food_id] = totals.get(item.food_id, 0.0) + float(item.grams)

    by_cat: dict[FoodCategory, list[ShoppingLine]] = {}
    for food_id, grams in totals.items():
        food = catalog[food_id]
        line = ShoppingLine(
            food_id=food_id,
            name_es=food.name_es,
            category=food.category,
            grams=round(grams, 1),
            unit_label=_unit_label(grams, food),
            ya_tengo=food_id in en_casa,
        )
        by_cat.setdefault(food.category, []).append(line)

    groups: list[ShoppingGroup] = []
    for category in _CATEGORY_ORDER:
        lines = by_cat.get(category)
        if not lines:
            continue
        # Lo que ya tiene, al final de su grupo: lo que se lee primero es lo que
        # hay que echar al carrito.
        lines.sort(key=lambda ln: (ln.ya_tengo, ln.name_es.lower()))
        groups.append(
            ShoppingGroup(
                category=category,
                label=_CATEGORY_LABEL[category],
                lines=tuple(lines),
            )
        )
    return groups


def _unit_label(grams: float, food: FoodItem) -> str | None:
    if food.unit_granularity is UnitGranularity.GRAMS or not food.default_unit_g:
        return None
    n = grams / food.default_unit_g
    # Redondeo amable para la compra (no porciones de plato).
    if food.unit_granularity is UnitGranularity.HALF:
        n = round(n * 2) / 2
    else:
        n = round(n)
    if n <= 0:
        return None
    name = food.unit_name or "unidad"
    if n == 1:
        return f"1 {name}"
    plural = name + ("s" if name[-1:] in "aeiou" else "es")
    label = f"{n:g}" if n % 1 else f"{int(n)}"
    return f"{label} {plural}"
