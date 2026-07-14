"""Validación de tolerancias (sección 11.4).

El contrato duro es el DÍA: kcal y los tres macros dentro de las tolerances
de la config. Por slot se validan proteína y carbohidrato contra el reparto que
usó el porcionador (`macro_split.macro_shares`): cada macro solo se le exige a
las comidas que tienen una fuente que lo lleve. La grasa, solo a nivel de día.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from nutriplan.domain.macro_split import flat_shares
from nutriplan.domain.models import MacroTargets, MealSlot
from nutriplan.domain.nutrition_config import NutritionConfig

# Por debajo de este valor absoluto (g) una desviación relativa no es señal:
# 12% de 8 g son 0.96 g — ruido de redondeo, no un plan descuadrado.
MIN_RELEVANT_G = 10.0

# Lo que le toca a una comida que el cliente no hace: nada.
_NO_SHARE = {"protein_g": 0.0, "carb_g": 0.0}


class MealLike(Protocol):
    """Lo único que la validación mira de una comida.

    Lo cumplen SolvedMeal (recién porcionada, dataclass) y MealEntry (leída de
    la base, pydantic): validar un plan persistido es tan válido como validar
    uno recién generado.
    """

    @property
    def slot(self) -> MealSlot: ...

    @property
    def computed(self) -> MacroTargets: ...


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
    solved: Sequence[MealLike],
    daily: MacroTargets,
    config: NutritionConfig,
    *,
    shares: Mapping[MealSlot, Mapping[str, float]] | None = None,
) -> list[Deviation]:
    """Valida un día. `shares` es el reparto por macro de `macro_split.macro_shares`.

    Sin `shares` se usa el peso plano del slot: es el reparto de siempre y vale
    cuando el llamador no tiene los alimentos a mano. Pero quien SÍ los tenga debe
    pasarlos, o juzgará al plan por un objetivo que el porcionador nunca persiguió:
    la proteína del snack de fruta la reparte el solver entre las comidas grandes,
    y sin `shares` el almuerzo parecería pasado de proteína.
    """
    tol = config.tolerances
    shares = shares or flat_shares(config)
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
        # Una comida que el cliente ya no hace (un plan viejo, de antes de quitar el
        # snack) no pesa nada: no se le exige macro alguno, y el día saldrá
        # desajustado —que es la verdad— en vez de reventar.
        share = shares.get(meal.slot, _NO_SHARE)
        slot_checks = (
            (
                "protein_g",
                meal.computed.protein_g,
                daily.protein_g * share["protein_g"],
                tol.protein_g,
            ),
            ("carb_g", meal.computed.carb_g, daily.carb_g * share["carb_g"], tol.carb_g),
        )
        for macro, actual, target, tolerance in slot_checks:
            if _off(actual, target, tolerance, floor=MIN_RELEVANT_G):
                deviations.append(Deviation(f"slot:{meal.slot.value}", macro, target, actual))
    return deviations


def day_totals(solved: Sequence[MealLike]) -> MacroTargets:
    return MacroTargets(
        kcal=round(sum(m.computed.kcal for m in solved), 1),
        protein_g=round(sum(m.computed.protein_g for m in solved), 1),
        carb_g=round(sum(m.computed.carb_g for m in solved), 1),
        fat_g=round(sum(m.computed.fat_g for m in solved), 1),
        fiber_g=round(sum(m.computed.fiber_g for m in solved), 1),
    )


def fiber_shortfall(solved: Sequence[MealLike], daily: MacroTargets) -> float:
    """Cuánta fibra le falta al día. 0.0 si llega al objetivo.

    NO es una desviación bloqueante, y es deliberado. La fibra depende de lo que
    al cliente le guste comer: negarle el plan a quien no soporta las legumbres
    sería absurdo. El motor ya garantiza la fruta por estructura (los dos snacks
    la exigen); esto informa del resto para que el entrenador decida — añadir
    avena, pan integral o frutos secos, o dejarlo así.
    """
    if daily.fiber_g <= 0:
        return 0.0
    actual = sum(m.computed.fiber_g for m in solved)
    return round(max(0.0, daily.fiber_g - actual), 1)


def slot_kcal_targets(daily: MacroTargets, config: NutritionConfig) -> dict[MealSlot, float]:
    return {slot: daily.kcal * pct for slot, pct in config.kcal_shares().items()}
