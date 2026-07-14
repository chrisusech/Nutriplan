"""Cuánto de cada macro le toca a cada comida.

El reparto de `config.meal_distribution` es el peso de la comida en el DÍA (el
almuerzo vale el 32%, un snack el 7.5%). Pero un peso no es un plato: un snack de
solo fruta no tiene de dónde sacar proteína, y exigirle su 7.5% deja el día corto
y el plan se rechaza.

Aquí cada macro se reparte SOLO entre las comidas que tienen una fuente de ese
macro, en proporción a su peso. La proteína que el snack no lleva no se pierde:
vuelve al desayuno, al almuerzo y a la cena. Es exactamente lo que la grasa ya
hacía (`portioning.py`, paso 2), generalizado a los otros dos.

Lo usan el porcionador (para pedir gramos) y la validación (para juzgarlos). Tienen
que mirar los MISMOS números: si el solver reparte la proteína del snack entre las
comidas grandes y el validador sigue esperando el 32% clavado en el almuerzo, el
plan que el solver produce es el que el validador rechaza.
"""

from collections.abc import Sequence

from nutriplan.domain.generation_rules import CARB_GROUP, PROTEIN_GROUP
from nutriplan.domain.models import FoodItem, MealSlot
from nutriplan.domain.nutrition_config import NutritionConfig

# Los dos macros que se reparten por comida. La grasa NO está: cierra a nivel de
# día y ya se reparte por fuente en el porcionador.
MACRO_SOURCES = {
    "protein_g": ("protein_100g", PROTEIN_GROUP),
    "carb_g": ("carb_100g", CARB_GROUP),
}

Meals = Sequence[tuple[MealSlot, list[FoodItem]]]


def _has_source(foods: list[FoodItem], attr: str, group: frozenset | set) -> bool:
    return any(
        f.category in group and not f.is_free and getattr(f, attr) > 0 for f in foods
    )


def _weight(config: NutritionConfig, slot: MealSlot, macro: str) -> float:
    """El peso declarado de una comida. Cero si el cliente ya no la hace.

    Pasa de verdad: el entrenador quita el snack de la tarde DESPUÉS de generar el
    plan. Ese plan sigue en la base con sus cinco comidas y hay que poder abrirlo —
    la comida que sobra no pesa nada, y la pantalla lo marcará como desajustado,
    que es la verdad: el plan quedó viejo.
    """
    share = config.meal_distribution.get(slot)
    return getattr(share, macro) if share else 0.0


def macro_shares(meals: Meals, config: NutritionConfig) -> dict[MealSlot, dict[str, float]]:
    """Peso de cada comida PARA CADA MACRO, renormalizado sobre quien tiene fuente.

    Devuelve, por slot, la fracción del macro diario que le corresponde. Un slot
    sin fuente de un macro recibe 0.0 de ese macro (y su cuota se reparte entre
    los demás), nunca un objetivo que no puede cumplir.
    """
    shares: dict[MealSlot, dict[str, float]] = {slot: {} for slot, _foods in meals}

    for macro, (attr, group) in MACRO_SOURCES.items():
        with_source = [
            slot for slot, foods in meals if _has_source(foods, attr, group)
        ]
        total = sum(_weight(config, slot, macro) for slot in with_source)
        for slot, _foods in meals:
            shares[slot][macro] = (
                _weight(config, slot, macro) / total
                if slot in with_source and total > 0
                else 0.0
            )
    return shares


def flat_shares(config: NutritionConfig) -> dict[MealSlot, dict[str, float]]:
    """El reparto sin mirar los alimentos: el peso declarado de cada comida.

    Es lo que se usaba antes de que un snack pudiera ser solo una fruta, y sigue
    siendo el default de `validate_day` cuando el llamador no tiene los alimentos.
    """
    return {
        slot: {macro: getattr(share, macro) for macro in MACRO_SOURCES}
        for slot, share in config.meal_distribution.items()
    }
