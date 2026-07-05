"""Validación de tolerancias (sección 11.4).

El contrato duro es el DÍA: kcal y los tres macros dentro de las tolerances
de la config. Por slot se validan proteína y carbohidrato contra el reparto
(la grasa se distribuye donde hay fuente real — ver portioning.py).
"""

from dataclasses import dataclass

from nutriplan.domain.models import MacroTargets, MealSlot
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import SolvedMeal

# Por debajo de este valor absoluto (g) una desviación relativa no es señal:
# 12% de 8 g son 0.96 g — ruido de redondeo, no un plan descuadrado.
MIN_RELEVANT_G = 10.0


@dataclass
class Deviation:
    scope: str  # "day" | "slot:<slot>"
    macro: str
    target: float
    actual: float

    def __str__(self) -> str:
        return f"{self.scope} {self.macro}: objetivo {self.target:.1f}, real {self.actual:.1f}"


def _off(actual: float, target: float, tolerance: float, *, floor: float = 0.0) -> bool:
    if target <= 0:
        return actual > max(floor, 5.0)
    if abs(actual - target) <= max(target * tolerance, floor):
        return False
    return True


def validate_day(
    solved: list[SolvedMeal], daily: MacroTargets, config: NutritionConfig
) -> list[Deviation]:
    tol = config.tolerances
    deviations: list[Deviation] = []

    totals = MacroTargets(
        kcal=round(sum(m.computed.kcal for m in solved), 1),
        protein_g=round(sum(m.computed.protein_g for m in solved), 1),
        carb_g=round(sum(m.computed.carb_g for m in solved), 1),
        fat_g=round(sum(m.computed.fat_g for m in solved), 1),
    )
    day_checks = (
        ("kcal", totals.kcal, daily.kcal, tol.kcal),
        ("protein_g", totals.protein_g, daily.protein_g, tol.protein_g),
        ("carb_g", totals.carb_g, daily.carb_g, tol.carb_g),
        ("fat_g", totals.fat_g, daily.fat_g, tol.fat_g),
    )
    for macro, actual, target, tolerance in day_checks:
        if _off(actual, target, tolerance):
            deviations.append(Deviation("day", macro, target, actual))

    for meal in solved:
        pct = config.meal_distribution[meal.slot]
        slot_checks = (
            ("protein_g", meal.computed.protein_g, daily.protein_g * pct, tol.protein_g),
            ("carb_g", meal.computed.carb_g, daily.carb_g * pct, tol.carb_g),
        )
        for macro, actual, target, tolerance in slot_checks:
            if _off(actual, target, tolerance, floor=MIN_RELEVANT_G):
                deviations.append(Deviation(f"slot:{meal.slot.value}", macro, target, actual))
    return deviations


def day_totals(solved: list[SolvedMeal]) -> MacroTargets:
    return MacroTargets(
        kcal=round(sum(m.computed.kcal for m in solved), 1),
        protein_g=round(sum(m.computed.protein_g for m in solved), 1),
        carb_g=round(sum(m.computed.carb_g for m in solved), 1),
        fat_g=round(sum(m.computed.fat_g for m in solved), 1),
    )


def slot_kcal_targets(daily: MacroTargets, config: NutritionConfig) -> dict[MealSlot, float]:
    return {slot: daily.kcal * pct for slot, pct in config.meal_distribution.items()}
