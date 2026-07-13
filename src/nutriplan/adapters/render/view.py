"""View-model del plan para render (PDF/DOCX comparten esta preparación)."""

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
ANOTACIONES_IMPORTANTES = [
    "Pesar los alimentos ya cocidos, con gramera.",
    "Tomar mínimo 2 litros de agua al día.",
    "La ensalada es libre: verduras verdes, tomate, cebolla, limón y especias sin restricción.",
    "Cocinar con poco aceite (medir el que indica el plan) y preferir aire, horno o plancha.",
    "Respetar los horarios de las 5 comidas; no saltarse ninguna.",
    "Endulzantes sin calorías permitidos con moderación; evitar azúcar y bebidas azucaradas.",
    "Este plan es un borrador profesional revisado y aprobado por tu entrenador(a).",
]


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
    """Filas = slots, columnas = 7 días de una fase."""
    grid: dict[MealSlot, list[CellView]] = {slot: [] for slot in SLOT_LABELS}
    for day in _days_for_phase(plan, phase):
        by_slot = {meal.slot: meal for meal in day.meals}
        for slot in SLOT_LABELS:
            meal = by_slot.get(slot)
            if meal is None:
                grid[slot].append(CellView(portions=[], extras=["—"], kcal=0.0))
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
