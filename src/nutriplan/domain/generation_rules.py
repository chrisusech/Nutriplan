"""Reglas de estructura de comidas (sección 11.1) — datos, no texto.

Cada slot tiene una "receta estructural": qué roles lleva (sin cantidades).
Grupos de macro dominante:
  P (proteína): PROTEIN + DAIRY · C (carbo): CARB + FRUIT · F (grasa): FAT
"""

from collections import defaultdict
from dataclasses import dataclass, field
from math import ceil

from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot, PlanSelection

PROTEIN_GROUP = {FoodCategory.PROTEIN, FoodCategory.DAIRY}
CARB_GROUP = {FoodCategory.CARB, FoodCategory.FRUIT}
FAT_GROUP = {FoodCategory.FAT}


@dataclass(frozen=True)
class SlotStructure:
    requires_protein: bool
    requires_carb: bool
    fruit_as_carb: bool = False  # snacks: el carbo es una fruta
    carb_optional: bool = False
    allows_fat_item: bool = True
    free_salad_default: bool = False
    max_items: int = 3
    description: str = ""


SLOT_STRUCTURE: dict[MealSlot, SlotStructure] = {
    MealSlot.BREAKFAST: SlotStructure(
        requires_protein=True,
        requires_carb=True,
        max_items=3,
        description="base de huevos/proteína + 1 carbohidrato + 1 grasa opcional",
    ),
    MealSlot.SNACK_AM: SlotStructure(
        requires_protein=True,
        requires_carb=True,
        fruit_as_carb=True,
        allows_fat_item=False,
        max_items=2,
        description="lácteo o proteína ligera + 1 fruta",
    ),
    MealSlot.LUNCH: SlotStructure(
        requires_protein=True,
        requires_carb=True,
        free_salad_default=True,
        max_items=3,
        description="proteína + carbohidrato + ensalada libre (± aguacate)",
    ),
    MealSlot.SNACK_PM: SlotStructure(
        requires_protein=True,
        requires_carb=True,
        fruit_as_carb=True,
        allows_fat_item=False,
        max_items=2,
        description="lácteo o proteína ligera + 1 fruta",
    ),
    MealSlot.DINNER: SlotStructure(
        requires_protein=True,
        requires_carb=True,
        carb_optional=True,
        free_salad_default=True,
        max_items=3,
        description="proteína + carbohidrato menor (opcional) + ensalada libre",
    ),
}


@dataclass
class StructureViolation:
    day_index: int
    slot: MealSlot
    reason: str


@dataclass
class VarietyViolation:
    food_name: str
    times_used: int
    limit: int
    slots: list[tuple[int, MealSlot]] = field(default_factory=list)


def validate_selection_structure(
    selection: PlanSelection, foods_by_id: dict[str, FoodItem]
) -> list[StructureViolation]:
    """La selección de la IA debe cubrir los roles de cada slot. Determinista."""
    violations: list[StructureViolation] = []
    seen_days = {d.day_index for d in selection.days}
    if seen_days != set(range(7)):
        violations.append(
            StructureViolation(-1, MealSlot.BREAKFAST, f"días incompletos: {sorted(seen_days)}")
        )

    for day in selection.days:
        seen_slots = {m.slot for m in day.meals}
        for slot in MealSlot:
            if slot not in seen_slots:
                violations.append(StructureViolation(day.day_index, slot, "slot faltante"))
        for meal in day.meals:
            rule = SLOT_STRUCTURE[meal.slot]
            foods = [foods_by_id[fid] for fid in meal.food_ids if fid in foods_by_id]
            if len(foods) != len(meal.food_ids):
                violations.append(
                    StructureViolation(day.day_index, meal.slot, "food_id fuera del catálogo")
                )
                continue
            categories = [f.category for f in foods]
            if len(foods) > rule.max_items:
                violations.append(
                    StructureViolation(
                        day.day_index, meal.slot, f"máximo {rule.max_items} alimentos"
                    )
                )
            if rule.requires_protein and not any(c in PROTEIN_GROUP for c in categories):
                violations.append(
                    StructureViolation(day.day_index, meal.slot, "falta fuente de proteína")
                )
            carb_ok = any(
                (c == FoodCategory.FRUIT if rule.fruit_as_carb else c in CARB_GROUP)
                for c in categories
            )
            if rule.requires_carb and not rule.carb_optional and not carb_ok:
                needed = "fruta" if rule.fruit_as_carb else "carbohidrato"
                violations.append(
                    StructureViolation(day.day_index, meal.slot, f"falta {needed}")
                )
            if not rule.allows_fat_item and any(c in FAT_GROUP for c in categories):
                violations.append(
                    StructureViolation(day.day_index, meal.slot, "grasa no permitida en snack")
                )
            if any(c == FoodCategory.VEGETABLE for c in categories):
                violations.append(
                    StructureViolation(
                        day.day_index,
                        meal.slot,
                        "las verduras van como ensalada libre, no porcionadas",
                    )
                )
    return violations


# La variedad se exige en TODOS los slots (el yogur no puede salir los 7 días en
# el snack) y para las categorías que definen la comida: proteína/lácteo, carbo,
# fruta. El límite es adaptativo — ver check_variety.
_VARIETY_CATEGORIES = {
    FoodCategory.PROTEIN,
    FoodCategory.DAIRY,
    FoodCategory.CARB,
    FoodCategory.FRUIT,
}


def check_variety(
    selection: PlanSelection,
    foods_by_id: dict[str, FoodItem],
    *,
    max_protein_repeats: int,
    max_carb_repeats: int,
) -> list[VarietyViolation]:
    """Variedad determinista y ADAPTATIVA a la diversidad disponible.

    Un alimento no debe repetirse en un slot más que el mínimo inevitable dado
    cuántas opciones distintas de su categoría entraron en ese slot: con 5
    proteínas distintas en el almuerzo, ninguna pasa de ⌈7/5⌉ = 2; con una sola
    opción (lista pobre del cliente), el límite se relaja a 7 en vez de fallar
    la generación. El tope de config actúa como cota superior deseada.
    """
    distinct: dict[tuple[MealSlot, FoodCategory], set[str]] = defaultdict(set)
    usage: dict[tuple[str, MealSlot], VarietyViolation] = {}
    for day in selection.days:
        for meal in day.meals:
            for fid in meal.food_ids:
                food = foods_by_id.get(fid)
                if food is None or food.category not in _VARIETY_CATEGORIES:
                    continue
                distinct[(meal.slot, food.category)].add(fid)
                entry = usage.setdefault(
                    (fid, meal.slot), VarietyViolation(food.name_es, 0, 0, [])
                )
                entry.times_used += 1
                entry.slots.append((day.day_index, meal.slot))

    ndays = len(selection.days) or 7
    violations: list[VarietyViolation] = []
    for (fid, slot), entry in usage.items():
        food = foods_by_id[fid]
        config_cap = max_protein_repeats if food.category in (
            FoodCategory.PROTEIN, FoodCategory.DAIRY
        ) else max_carb_repeats
        n_distinct = len(distinct[(slot, food.category)]) or 1
        # mínimo inevitable dado el pool; si es menor que el tope de config, se
        # exige el tope; si el pool es tan pobre que obliga a repetir, se relaja.
        limit = max(config_cap, ceil(ndays / n_distinct))
        entry.limit = limit
        if entry.times_used > limit:
            violations.append(entry)
    return violations
