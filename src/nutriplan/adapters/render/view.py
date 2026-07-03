"""View-model del plan para render (PDF/DOCX comparten esta preparación)."""

from dataclasses import dataclass
from uuid import UUID

from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import FoodItem, MealSlot, PlanCycle, PlanPhase

SLOT_LABELS: dict[MealSlot, str] = {
    MealSlot.BREAKFAST: "Desayuno",
    MealSlot.SNACK_AM: "Snack AM",
    MealSlot.LUNCH: "Almuerzo",
    MealSlot.SNACK_PM: "Snack PM",
    MealSlot.DINNER: "Cena",
}
DAY_LABELS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
PHASE_LABELS = {PlanPhase.FIRST_15: "Días 1 – 15", PlanPhase.NEXT_15: "Días 16 – 30"}

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


def natural_units(grams: float, food: FoodItem) -> str | None:
    if not food.default_unit_g:
        return None
    units = grams / food.default_unit_g
    rounded = round(units * 2) / 2  # a medias unidades
    if 0.5 <= rounded <= 6 and abs(units - rounded) / max(units, 0.01) < 0.25:
        n = int(rounded) if float(rounded).is_integer() else rounded
        return f"≈ {n} und"
    return None


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
                grams = int(p.grams) if float(p.grams).is_integer() else p.grams
                text = f"{food.name_es.capitalize()} — {grams} g"
                if units := natural_units(p.grams, food):
                    text += f" ({units})"
                portions.append(PortionView(text=text))
            extras = []
            if meal.free_protein:
                extras.append("Proteína libre")
            if meal.free_salad:
                extras.append("Ensalada libre")
            grid[slot].append(CellView(portions=portions, extras=extras, kcal=meal.computed.kcal))
    return grid
