"""Afinidad de alimentos por comida (qué va bien en cada slot).

El porcionador cuadra los MACROS; este módulo cuida que las combinaciones sean
APETITOSAS y realistas, no solo correctas en números: el desayuno gira en torno
a los huevos (+ lácteos/batido como variación), los snacks son ligeros (lácteo +
fruta, nunca carne), y las carnes, pescados y mariscos viven en almuerzo y cena.

La afinidad es un DATO del alimento (`FoodItem.meal_slots`), no una lista de
nombres en el código. Antes esto era un `frozenset` literal con "avena en
hojuelas", "pan de masa madre"...: añadir un alimento a la base exigía editar
Python, y un alimento del tenant nunca podía ser de desayuno.
"""

from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot, UnitGranularity


def allows(food: FoodItem, slot: MealSlot) -> bool:
    return slot in food.meal_slots


def is_egg(food: FoodItem) -> bool:
    return "huevo" in food.tags


def is_shake(food: FoodItem) -> bool:
    return "batido" in food.tags


def breakfast_protein(food: FoodItem) -> bool:
    """Proteína apta para desayuno: huevos, lácteos o batido — no carnes/pescado."""
    return (
        allows(food, MealSlot.BREAKFAST)
        and (is_egg(food) or is_shake(food) or food.category is FoodCategory.DAIRY)
    )


def snack_protein(food: FoodItem) -> bool:
    """Proteína apta para snack: lácteo, batido o loncha pequeña. Ligero.

    El HUEVO no. Un huevo duro no es lo que nadie come a media mañana, y el motor
    lo ponía porque el snack tenía que aportar su cuota de proteína como cualquier
    otra comida. Ya no: el snack es saciedad y las comidas grandes cierran los
    macros (ver `macro_split.macro_shares`).
    """
    if not allows(food, MealSlot.SNACK_AM):
        return False
    if is_egg(food):
        return False
    if is_shake(food) or food.category is FoodCategory.DAIRY:
        return True
    # lonchas unitarias (jamón, queso fresco) — no carnes de plato principal
    return (
        food.category is FoodCategory.PROTEIN
        and food.unit_granularity is UnitGranularity.WHOLE
        and food.default_unit_g is not None
        and food.default_unit_g <= 35
    )


def main_protein(food: FoodItem) -> bool:
    """Proteína de plato principal: carnes, pescado, mariscos, legumbres, tofu."""
    return (
        food.category is FoodCategory.PROTEIN
        and allows(food, MealSlot.LUNCH)
        and not is_egg(food)
        and not is_shake(food)
    )


def is_breakfast_carb(food: FoodItem) -> bool:
    return food.category is FoodCategory.CARB and allows(food, MealSlot.BREAKFAST)


def is_main_carb(food: FoodItem) -> bool:
    return food.category is FoodCategory.CARB and allows(food, MealSlot.LUNCH)
