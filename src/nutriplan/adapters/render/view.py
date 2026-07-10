"""View-model del plan para render (PDF/DOCX comparten esta preparación)."""

from dataclasses import dataclass
from uuid import UUID

from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import (
    FoodItem,
    MealSlot,
    PlanCycle,
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
    plan: PlanCycle, foods: dict[UUID, FoodItem]
) -> dict[MealSlot, list[CellView]]:
    """Filas = slots, columnas = 7 días."""
    grid: dict[MealSlot, list[CellView]] = {slot: [] for slot in SLOT_LABELS}
    for day in sorted(plan.days, key=lambda d: d.day_index):
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
            grid[slot].append(CellView(portions=portions, extras=extras, kcal=meal.computed.kcal))
    return grid
