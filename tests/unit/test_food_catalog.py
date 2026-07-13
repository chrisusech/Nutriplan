"""Invariantes del catálogo curado.

El catálogo es la única fuente de verdad de los alimentos y ahora carga bastante
más peso que antes: además de los macros, decide en qué comidas puede aparecer
un alimento y a qué múltiplo redondea sus gramos. Un typo en una fila (una coma
corrida, un slot mal escrito) deja de ser un dato feo y pasa a ser un plan roto
para una clienta. Estos tests son la barrera.
"""

from math import isclose
from pathlib import Path

import pytest

from nutriplan.adapters.food.curated_loader import load_curated_foods
from nutriplan.domain.generation_rules import (
    CARB_GROUP,
    FAT_GROUP,
    PROTEIN_GROUP,
    SLOT_STRUCTURE,
)
from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot, UnitGranularity

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "foods" / "curated_foods.csv"


@pytest.fixture(scope="module")
def catalog() -> list[FoodItem]:
    return load_curated_foods(CSV_PATH)


def test_every_food_belongs_to_at_least_one_meal(catalog) -> None:
    """Un alimento sin slots es invisible para el generador: nunca se elegiría."""
    for food in catalog:
        assert food.meal_slots, f"{food.name_es}: sin meal_slots"


def test_free_foods_carry_no_macros_and_do_carry_a_label(catalog) -> None:
    for food in catalog:
        if food.is_free:
            assert food.category is FoodCategory.OTHER, food.name_es
            assert food.kcal_100g == 0, food.name_es
            assert food.protein_100g == food.carb_100g == food.fat_100g == 0, food.name_es
            assert food.free_text, f"{food.name_es}: libre pero sin texto que imprimir"
        else:
            assert food.free_text is None, f"{food.name_es}: no es libre pero trae free_text"


def test_fiber_is_a_subset_of_the_carbohydrate(catalog) -> None:
    """La fibra es carbohidrato no digerible: no puede haber más fibra que carbo.

    Es el detector de decimales corridos más barato que hay (14.5 vs 145).
    """
    for food in catalog:
        assert food.fiber_100g <= food.carb_100g + 1e-6, (
            f"{food.name_es}: fibra {food.fiber_100g} > carbo {food.carb_100g}"
        )


def test_macros_roughly_reconstruct_the_calories(catalog) -> None:
    """Atwater (4/4/9) como detector de typos, no como verdad exacta.

    La fibra y los alcoholes de azúcar hacen que el cuadre nunca sea perfecto, así
    que la tolerancia es amplia a propósito: aquí solo se busca la coma corrida.
    """
    for food in catalog:
        if food.is_free:
            continue
        atwater = food.protein_100g * 4 + food.carb_100g * 4 + food.fat_100g * 9
        assert isclose(atwater, food.kcal_100g, rel_tol=0.25), (
            f"{food.name_es}: kcal {food.kcal_100g} vs Atwater {atwater:.0f}"
        )


def test_portion_steps_are_consistent_with_the_unit(catalog) -> None:
    """Lo contable salta de unidad; lo que se pesa, de a poco.

    Si un alimento contable tuviera un paso que no es su unidad, el plan pediría
    '1.4 huevos'.
    """
    for food in catalog:
        assert food.portion_step_g > 0, food.name_es
        if food.unit_granularity is UnitGranularity.WHOLE and food.default_unit_g:
            assert food.portion_step_g == food.default_unit_g, (
                f"{food.name_es}: contable entero, pero el paso no es su unidad"
            )
        if food.unit_granularity is UnitGranularity.HALF and food.default_unit_g:
            assert food.portion_step_g == food.default_unit_g / 2, (
                f"{food.name_es}: admite medias unidades, pero el paso no es media unidad"
            )
        if food.portion_min_g is not None:
            assert food.portion_min_g >= food.portion_step_g, (
                f"{food.name_es}: piso {food.portion_min_g} bajo el paso {food.portion_step_g}"
            )


@pytest.mark.parametrize("slot", list(MealSlot))
def test_every_slot_can_be_filled_from_the_catalog(catalog, slot) -> None:
    """Cada comida necesita fuentes reales de lo que su estructura exige.

    Sin esto, el catálogo puede quedar sintácticamente perfecto y aun así ser
    imposible de porcionar — y el fallo aparecería en `generate_cycle`, cuatro
    capas más abajo y sin decir por qué.
    """
    rule = SLOT_STRUCTURE[slot]
    here = [f for f in catalog if slot in f.meal_slots and not f.is_free]

    if rule.requires_protein:
        assert [f for f in here if f.category in PROTEIN_GROUP], f"{slot.value}: sin proteína"
    if rule.requires_carb:
        group = {FoodCategory.FRUIT} if rule.fruit_as_carb else CARB_GROUP
        assert [f for f in here if f.category in group], f"{slot.value}: sin carbohidrato"
    if rule.allows_fat_item:
        assert [f for f in here if f.category in FAT_GROUP], f"{slot.value}: sin grasa"


def test_a_source_of_a_macro_actually_provides_that_macro(catalog) -> None:
    """El solver divide por la densidad del macro: un carbo con 0 g de carbo es
    una división por cero disfrazada (`GenerationError` en portioning.py)."""
    density = {
        FoodCategory.PROTEIN: "protein_100g",
        FoodCategory.CARB: "carb_100g",
        FoodCategory.FAT: "fat_100g",
        FoodCategory.FRUIT: "carb_100g",
        FoodCategory.DAIRY: "protein_100g",
    }
    for food in catalog:
        attr = density.get(food.category)
        if attr is None or food.is_free:
            continue
        assert getattr(food, attr) > 0, f"{food.name_es}: {food.category.value} sin {attr}"
