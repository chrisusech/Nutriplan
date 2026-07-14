"""Reglas de estructura de comidas (sección 11.1) — datos, no texto.

Cada slot tiene una "receta estructural": qué roles lleva (sin cantidades).
Grupos de macro dominante:
  P (proteína): PROTEIN + DAIRY · C (carbo): CARB + FRUIT · F (grasa): FAT
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from math import ceil

from nutriplan.domain.models import (
    DaySelection,
    FoodCategory,
    FoodItem,
    MealSlot,
    PlanSelection,
)

PROTEIN_GROUP = {FoodCategory.PROTEIN, FoodCategory.DAIRY}
CARB_GROUP = {FoodCategory.CARB, FoodCategory.FRUIT}
FAT_GROUP = {FoodCategory.FAT}


@dataclass(frozen=True)
class SlotStructure:
    requires_protein: bool
    requires_carb: bool
    # Los snacks exigían que su carbohidrato fuera FRUTA, sin más. Con eso, "atún
    # con galletas de arroz" era estructuralmente ilegal. Lo que hace coherente un
    # snack es el PLATO (nadie escribió "yogur con pan"), no prohibir los carbos.
    fruit_as_carb: bool = False
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
    # Un snack no es una comida en pequeño: es saciedad. Puede ser una fruta sola,
    # una manzana con crema de almendras o un yogur griego con fruta. Exigirle
    # proteína Y carbohidrato como a un almuerzo es lo que obligaba al motor a
    # poner huevo duro a media mañana. Lo que no lleve, lo compensan las tres
    # comidas grandes (ver `macro_split.macro_shares`).
    MealSlot.SNACK_AM: SlotStructure(
        requires_protein=False,
        requires_carb=False,
        max_items=2,
        description="ligero: 1 fruta, o fruta + crema de frutos secos, o lácteo + fruta",
    ),
    MealSlot.LUNCH: SlotStructure(
        requires_protein=True,
        requires_carb=True,
        free_salad_default=True,
        max_items=3,
        description="proteína + carbohidrato + ensalada libre (± aguacate)",
    ),
    MealSlot.SNACK_PM: SlotStructure(
        requires_protein=False,
        requires_carb=False,
        max_items=2,
        description="ligero: 1 fruta, o fruta + crema de frutos secos, o lácteo + fruta",
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


def drop_free_meal(
    selection: PlanSelection, free_meal: tuple[int, MealSlot] | None
) -> PlanSelection:
    """Quita de la selección la celda que es comida libre.

    El selector (y desde luego el LLM real) emite las cinco comidas de cada día:
    no sabe nada de comidas libres, ni tiene por qué. La celda se quita AQUÍ, en la
    frontera, y a partir de este punto el pipeline entero —estructura, variedad,
    porcionado, validación— trabaja con un día de cuatro comidas y no necesita
    enterarse de nada.
    """
    if free_meal is None:
        return selection
    day_index, slot = free_meal
    return PlanSelection(
        days=[
            DaySelection(
                day_index=day.day_index,
                meals=[
                    meal
                    for meal in day.meals
                    if not (day.day_index == day_index and meal.slot is slot)
                ],
            )
            for day in selection.days
        ]
    )


def validate_selection_structure(
    selection: PlanSelection,
    foods_by_id: dict[str, FoodItem],
    slots: Sequence[MealSlot] | None = None,
    free_meal: tuple[int, MealSlot] | None = None,
) -> list[StructureViolation]:
    """La selección de la IA debe cubrir los roles de cada slot. Determinista.

    `slots` son las comidas que come este cliente (5 si no se dice otra cosa): un
    plan de cuatro comidas no tiene un "snack PM faltante".

    `free_meal` es la única comida de la semana que PUEDE faltar: la libre. No se
    la juzga porque no tiene nada que juzgar — ni alimentos, ni roles, ni macros.
    """
    expected = list(slots) if slots else list(MealSlot)
    violations: list[StructureViolation] = []
    seen_days = {d.day_index for d in selection.days}
    if seen_days != set(range(7)):
        violations.append(
            StructureViolation(-1, MealSlot.BREAKFAST, f"días incompletos: {sorted(seen_days)}")
        )

    for day in selection.days:
        seen_slots = {m.slot for m in day.meals}
        for slot in expected:
            if slot not in seen_slots and (day.day_index, slot) != free_meal:
                violations.append(StructureViolation(day.day_index, slot, "slot faltante"))
        for slot in seen_slots - set(expected):
            violations.append(
                StructureViolation(day.day_index, slot, "el cliente no come esta comida")
            )
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
    available: Mapping[tuple[MealSlot, FoodCategory], int] | None = None,
) -> list[VarietyViolation]:
    """Variedad determinista, adaptada a lo que el cliente TIENE DISPONIBLE.

    Dos cosas estaban mal y se tapaban entre sí:

    1. Se contaba por `(alimento, slot)`, así que el mismo yogur en el snack de
       la mañana y en el de la tarde eran DOS contadores independientes: 14 usos
       en una semana pasaban limpios.

    2. El límite se derivaba de las opciones que el selector había ELEGIDO, no de
       las que había. Si el selector repetía un solo alimento, `n_distinct` valía
       1, el límite se relajaba a 7 y 7 usos de 7 no eran violación. La regla
       absolvía justo el caso que existía para detectar — la repetición total— y
       solo castigaba la moderada. Un bucle que se auto-justificaba.

    Ahora se cuenta por alimento en TODA la semana, y el límite se deriva de lo que
    el cliente TIENE PARA CADA SLOT — no del total de su categoría. La diferencia
    importa: un cliente puede tener tres proteínas y que solo una (el huevo) pueda
    ir a un snack, porque las otras dos son pollo y atún. Contar tres cuando de
    verdad hay una haría fallar la generación por una repetición que era
    inevitable.

    Con una lista pobre el límite es laxo a propósito: repetir es entonces
    inevitable y reventar la generación no ayuda a nadie. Lo que se hace en ese
    caso es AVISAR al entrenador (`meal_template.pool_health`) para que amplíe la
    lista. La variedad de verdad la produce el motor de platos con su función de
    coste; esta regla solo es la red que impide que el motor se acomode.
    """
    available = available or {}
    usage: dict[str, VarietyViolation] = {}
    # Cuántas comidas de la semana necesitaron cada (slot, categoría).
    demand: Counter[tuple[MealSlot, FoodCategory]] = Counter()

    for day in selection.days:
        for meal in day.meals:
            categories_here: set[FoodCategory] = set()
            for fid in meal.food_ids:
                food = foods_by_id.get(fid)
                if food is None or food.category not in _VARIETY_CATEGORIES:
                    continue
                categories_here.add(food.category)
                entry = usage.setdefault(fid, VarietyViolation(food.name_es, 0, 0, []))
                entry.times_used += 1
                entry.slots.append((day.day_index, meal.slot))
            for category in categories_here:
                demand[(meal.slot, category)] += 1

    violations: list[VarietyViolation] = []
    for fid, entry in usage.items():
        food = foods_by_id[fid]
        is_protein = food.category in (FoodCategory.PROTEIN, FoodCategory.DAIRY)
        config_cap = max_protein_repeats if is_protein else max_carb_repeats

        # El techo se calcula POR SLOT —sobre los slots donde el alimento SALE— y
        # se suma. El tope de config ("la misma proteína, máximo 3 veces por
        # semana") se escribió cuando el conteo era por slot; aplicado de golpe a
        # la semana entera significaría algo mucho más estricto: un huevo, que
        # puede ir al desayuno, a los dos snacks y a la cena, no puede tener el
        # mismo techo semanal que un salmón que solo va a almuerzo y cena.
        # En cada slot el alimento puede salir como mucho el tope de config, o más
        # si repetir ahí es inevitable porque no hay alternativas.
        limit = 0
        for slot in {slot for _day, slot in entry.slots}:
            needed = demand.get((slot, food.category), 0)
            options = available.get((slot, food.category), 0) or 1
            limit += max(config_cap, ceil(needed / options))

        entry.limit = max(config_cap, limit)
        if entry.times_used > entry.limit:
            violations.append(entry)
    return violations


def slot_availability(
    allowed: Sequence[FoodItem],
    usable: Callable[[FoodItem, MealSlot], bool] | None = None,
) -> dict[tuple[MealSlot, FoodCategory], int]:
    """Cuántos alimentos de cada categoría puede el cliente poner en cada slot.

    `usable` descuenta los que declaran el slot pero el motor no puede usar ahí.
    La diferencia decide planes: una lata de atún (100 g exactos, no se parte) no
    cuadra un almuerzo de 36 g de proteína, así que el almuerzo de un cliente con
    pollo y atún tiene UNA opción, no dos. Contando dos, el techo de repeticiones
    del pollo sale a la mitad de lo que es inevitable y la generación entera falla
    por una repetición que nadie podía evitar.
    """
    counts: Counter[tuple[MealSlot, FoodCategory]] = Counter()
    for food in allowed:
        for slot in food.meal_slots:
            if usable is not None and not usable(food, slot):
                continue
            counts[(slot, food.category)] += 1
    return dict(counts)
