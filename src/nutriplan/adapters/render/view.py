"""View-model del plan para render (PDF/DOCX comparten esta preparación)."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import (
    FoodItem,
    MacroTargets,
    MealSlot,
    PlanCycle,
    PlanPhase,
    UnitGranularity,
)

SLOT_LABELS: dict[MealSlot, str] = {
    MealSlot.BREAKFAST: "Desayuno",
    MealSlot.SNACK_AM: "Snack AM",
    MealSlot.LUNCH: "Almuerzo",
    MealSlot.SNACK_PM: "Snack PM",
    MealSlot.DINNER: "Cena",
}
DAY_LABELS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
WEEK_SUBTITLE = "Plan semanal · 7 días"
PHASE_SUBTITLES: dict[PlanPhase, str] = {
    PlanPhase.FIRST_15: "Semana 1 · días 1-15",
    PlanPhase.NEXT_15: "Semana 2 · días 16-30",
}


def _days_for_phase(plan: PlanCycle, phase: PlanPhase | None = None) -> list:
    days = plan.days
    if phase is not None:
        days = [d for d in days if d.phase == phase]
    return sorted(days, key=lambda d: d.day_index)


def plan_phases_in(plan: PlanCycle) -> list[PlanPhase]:
    """Fases presentes en el plan; planes de 30 días siempre incluyen las 2 semanas."""
    if plan.duration_days >= 30:
        return [PlanPhase.FIRST_15, PlanPhase.NEXT_15]
    present = sorted({d.phase for d in plan.days}, key=lambda p: p.value)
    return present or [PlanPhase.FIRST_15]
SLOT_TIMES: dict[MealSlot, str] = {
    MealSlot.BREAKFAST: "7:00 am",
    MealSlot.SNACK_AM: "10:30 am",
    MealSlot.LUNCH: "1:00 pm",
    MealSlot.SNACK_PM: "4:30 pm",
    MealSlot.DINNER: "7:30 pm",
}

# Sección fija del formato del negocio (sección 12.2).
#
# La versión anterior decía "las proteínas se pesan en CRUDO", y era FALSO respecto
# a lo que el plan calcula: el catálogo lleva los macros del alimento COCIDO (el
# pollo, 165 kcal y 31 g de proteína por 100 g — en crudo son 120 y 22.5). Quien
# pesara 120 g de pollo crudo se comía ~85 g cocidos: un 30% menos de proteína de
# la que el plan le prometía. El texto ahora dice lo que los números hacen.
_ANOTACIONES_BASE = [
    "Todos los pesos son del alimento YA COCIDO: pesa la porción lista para comer, "
    "no cruda (el pollo, el arroz, la pasta, etc.).",
    "El plan se cuadra sobre el total de la SEMANA, así que hay días que quedan un "
    "poco por encima o por debajo del objetivo diario. Es normal: la semana cierra.",
    "Los huevos van por unidades, y los puedes preparar como gustes, incluso con "
    "los vegetales que desees, excepto fritos (evitar exceso de aceite).",
    "La gelatina sin azúcar la puedes comer cuando quieras.",
    "Puedes comer 3 cuadritos de chocolate 80% cacao todos los días, MENOS sábado "
    "y domingo.",
    # Aquí vivía "La comida libre puedes hacerla el sábado o el domingo, según
    # prefieras." — en TODOS los planes, tuvieran comida libre o no, porque la
    # comida libre no existía como dato y esto era lo más parecido a tenerla. Ahora
    # la frase la escribe `anotaciones()` con el día y la comida de verdad, y si el
    # plan no tiene comida libre no promete ninguna.
    "Alimentos libres: café sin azúcar, aromática sin azúcar, todos los vegetales "
    "que quieras y bebidas sin azúcar ni calorías.",
    "Las comidas son cada 2 a 3 horas.",
]


# Como se nombran las comidas DENTRO de una frase (en la rejilla van con mayúscula).
SLOT_NAMES: dict[MealSlot, str] = {
    MealSlot.BREAKFAST: "desayuno",
    MealSlot.SNACK_AM: "snack AM",
    MealSlot.LUNCH: "almuerzo",
    MealSlot.SNACK_PM: "snack PM",
    MealSlot.DINNER: "cena",
}

# Con su artículo, para las frases. La cena es LA cena: "tu comida libre es el
# domingo en el cena" es lo que sale de concatenar un artículo fijo con el nombre.
SLOT_NAMES_ARTICLE: dict[MealSlot, str] = {
    MealSlot.BREAKFAST: "el desayuno",
    MealSlot.SNACK_AM: "el snack AM",
    MealSlot.LUNCH: "el almuerzo",
    MealSlot.SNACK_PM: "el snack PM",
    MealSlot.DINNER: "la cena",
}


def anotaciones(
    slots: Sequence[MealSlot], free_meal: tuple[int, MealSlot] | None = None
) -> list[str]:
    """Las anotaciones del plan. La primera cuenta las comidas que de verdad tiene.

    No todos los clientes comen cinco veces: prometerle cinco comidas a quien
    recibió cuatro es la clase de detalle por la que el plan deja de parecer suyo.

    Y la comida libre se nombra por su día y su comida, o no se nombra. Antes se
    prometía "el sábado o el domingo" en todos los planes, incluidos los que no
    tenían ninguna.
    """
    chosen = set(slots)
    names = [SLOT_NAMES[s] for s in SLOT_NAMES if s in chosen]
    listed = f"{', '.join(names[:-1])} y {names[-1]}" if len(names) > 1 else names[0]
    notes = [f"El plan consta de {len(names)} comidas: {listed}.", *_ANOTACIONES_BASE]
    if free_meal is not None:
        day, slot = free_meal
        notes.append(
            f"Tu comida libre es el {DAY_LABELS[day].lower()} en "
            f"{SLOT_NAMES_ARTICLE[slot]}: come lo que quieras, sin pesar nada. Las "
            "demás comidas de ese día siguen igual."
        )
    return notes


def slots_in(plan: PlanCycle) -> list[MealSlot]:
    """Las comidas que este plan tiene, en el orden del día."""
    present = {meal.slot for day in plan.days for meal in day.meals}
    return [slot for slot in SLOT_LABELS if slot in present]


def free_meal_cell(plan: PlanCycle) -> tuple[int, MealSlot] | None:
    """La comida libre de este plan (día, comida), leída del propio plan.

    El PDF no necesita al cliente: la verdad de un plan ya generado está en sus
    filas. Si mañana el entrenador mueve la comida libre, este plan sigue diciendo
    lo que decía cuando se firmó.
    """
    for day in sorted(plan.days, key=lambda d: d.day_index):
        for meal in day.meals:
            if meal.is_free_meal:
                return (day.day_index, meal.slot)
    return None

# Encabezado de cada rejilla, con el tono de los planes reales.
PHASE_INTRO: dict[PlanPhase, str] = {
    PlanPhase.FIRST_15: "Para tus primeros 15 días, tu plan personalizado está "
                        "inspirado en lo que te gusta:",
    PlanPhase.NEXT_15: "Para tus próximos 15 días, tu plan personalizado está "
                       "inspirado en lo que te gusta:",
}


@dataclass
class PortionView:
    text: str  # "Pechuga de pollo — 120 g (≈ 1 porción)"


@dataclass
class CellView:
    portions: list[PortionView]
    extras: list[str]  # "Ensalada libre", "Proteína libre"
    kcal: float
    protein_g: float = 0.0
    carb_g: float = 0.0
    fat_g: float = 0.0
    macro_line: str = ""  # preformateado para Jinja/PDF
    free_meal: bool = False  # LA comida libre: sin porciones y sin macros


def cell_for_template(cell: CellView) -> dict[str, object]:
    """Dict plano: Jinja no depende de atributos opcionales del dataclass."""
    return {
        "portions": cell.portions,
        "extras": cell.extras,
        "kcal": cell.kcal,
        "protein_g": cell.protein_g,
        "carb_g": cell.carb_g,
        "fat_g": cell.fat_g,
        "macro_line": cell.macro_line,
        "free_meal": cell.free_meal,
    }


@dataclass
class DayTotalsView:
    kcal: int
    protein_g: int
    carb_g: int
    fat_g: int


def macro_line(m: MacroTargets, *, kcal: bool = True) -> str:
    """Una línea legible: kcal + P/C/G."""
    parts: list[str] = []
    if kcal:
        parts.append(f"{round(m.kcal)} kcal")
    parts.append(f"P {round(m.protein_g)} g")
    parts.append(f"C {round(m.carb_g)} g")
    parts.append(f"G {round(m.fat_g)} g")
    return " · ".join(parts)


def macro_compact(m: MacroTargets) -> str:
    """Macros sin kcal, para celdas pequeñas."""
    return (
        f"P {round(m.protein_g)} · "
        f"C {round(m.carb_g)} · "
        f"G {round(m.fat_g)}"
    )


@dataclass
class MealCard:
    slot_label: str
    time: str
    kcal: int
    items: list[str]  # porciones con unidad natural + extras (ensalada libre)


@dataclass
class DayCard:
    n: int
    name: str
    kcal: int
    protein_g: int
    carb_g: int
    fat_g: int
    meals: list[MealCard]


def build_week(
    plan: PlanCycle, foods: dict[UUID, FoodItem], phase: PlanPhase | None = None
) -> list[DayCard]:
    """View-model del PDF: tarjetas de día (una fase a la vez)."""
    cards: list[DayCard] = []
    for day in _days_for_phase(plan, phase):
        by_slot = {m.slot: m for m in day.meals}
        meals: list[MealCard] = []
        for slot in SLOT_LABELS:
            meal = by_slot.get(slot)
            if meal is None:
                continue
            if meal.is_free_meal:
                meals.append(MealCard(
                    slot_label=SLOT_LABELS[slot], time=SLOT_TIMES[slot],
                    kcal=0, items=["COMIDA LIBRE — come lo que quieras"],
                ))
                continue
            items = []
            for p in meal.portions:
                food = foods.get(p.food_id)
                if food is None:
                    raise RenderError(f"Alimento {p.food_id} del plan no está en el catálogo")
                items.append(portion_text(p.grams, food))
            if meal.free_protein:
                items.append("Proteína libre")
            if meal.free_salad:
                items.append("Ensalada libre")
            meals.append(MealCard(
                slot_label=SLOT_LABELS[slot], time=SLOT_TIMES[slot],
                kcal=round(meal.computed.kcal), items=items,
            ))
        t = day.totals
        cards.append(DayCard(
            n=day.day_index + 1, name=DAY_LABELS[day.day_index],
            kcal=round(t.kcal), protein_g=round(t.protein_g),
            carb_g=round(t.carb_g), fat_g=round(t.fat_g), meals=meals,
        ))
    return cards


def _fmt_count(n: float) -> str:
    """1 → '1', 0.5 → '½', 1.5 → '1½', 3 → '3'."""
    whole, frac = divmod(round(n * 2), 2)
    half = "½" if frac else ""
    if whole == 0:
        return half or "0"
    return f"{whole}{half}"


def _plural(name: str, n: float) -> str:
    if n <= 1:
        return name
    return name + ("s" if name[-1:] in "aeiou" else "es")  # lata→latas, unidad→unidades


def natural_units(grams: float, food: FoodItem) -> str | None:
    """Etiqueta de unidades para alimentos contables ('3 huevos', '1 lata').

    Solo para `whole`/`half`; sus gramos ya vienen cuantizados por el solver, así
    que el conteo es exacto — nada de '≈ 5.5 und'. Devuelve None en gramos libres.
    """
    if food.unit_granularity is UnitGranularity.GRAMS or not food.default_unit_g:
        return None
    n = grams / food.default_unit_g
    name = food.unit_name or "unidad"
    return f"{_fmt_count(n)} {_plural(name, n)}"


def portion_text(grams: float, food: FoodItem) -> str:
    """Texto de una porción, con la unidad natural al frente si aplica."""
    g = int(grams) if float(grams).is_integer() else round(grams, 1)
    label = natural_units(grams, food)
    if label is None:
        return f"{food.name_es.capitalize()} — {g} g"
    if food.unit_name and food.unit_name in food.name_es.lower():
        head = label  # "3 huevos" — la unidad ya nombra el alimento
    else:
        head = f"{label} de {food.name_es}"  # "1 lata de atún en agua"
    return f"{head[:1].upper()}{head[1:]} ({g} g)"


def build_grid(
    plan: PlanCycle,
    foods: dict[UUID, FoodItem],
    phase: PlanPhase | None = None,
) -> dict[MealSlot, list[CellView]]:
    """Filas = las comidas que el plan tiene, columnas = 7 días de una fase."""
    slots = slots_in(plan)
    grid: dict[MealSlot, list[CellView]] = {slot: [] for slot in slots}
    for day in _days_for_phase(plan, phase):
        by_slot = {meal.slot: meal for meal in day.meals}
        for slot in slots:
            meal = by_slot.get(slot)
            if meal is None:
                grid[slot].append(CellView(portions=[], extras=["—"], kcal=0.0))
                continue
            if meal.is_free_meal:
                # Sin porciones, sin macros y sin línea de kcal: la columna de
                # totales de ese día sale más baja, y eso es exactamente la verdad.
                grid[slot].append(CellView(portions=[], extras=[], kcal=0.0,
                                           free_meal=True))
                continue
            portions = []
            for p in meal.portions:
                food = foods.get(p.food_id)
                if food is None:
                    raise RenderError(f"Alimento {p.food_id} del plan no está en el catálogo")
                portions.append(PortionView(text=portion_text(p.grams, food)))
            extras = []
            if meal.free_protein:
                extras.append("Proteína libre")
            if meal.free_salad:
                extras.append("Ensalada libre")
            c = meal.computed
            macro = (
                f"{round(c.kcal)} kcal · {macro_compact(c)}"
                if c.kcal
                else ""
            )
            grid[slot].append(CellView(
                portions=portions, extras=extras, kcal=c.kcal,
                protein_g=c.protein_g, carb_g=c.carb_g, fat_g=c.fat_g,
                macro_line=macro,
            ))
    return grid


def day_totals_row(
    plan: PlanCycle, phase: PlanPhase | None = None
) -> list[DayTotalsView]:
    """Totales diarios por columna de la rejilla (7 días)."""
    row: list[DayTotalsView] = []
    for day in _days_for_phase(plan, phase):
        t = day.totals
        row.append(DayTotalsView(
            kcal=round(t.kcal), protein_g=round(t.protein_g),
            carb_g=round(t.carb_g), fat_g=round(t.fat_g),
        ))
    return row
