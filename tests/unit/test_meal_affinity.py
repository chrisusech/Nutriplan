"""Afinidad por comida: qué alimento va bien en cada slot (calidad culinaria)."""

from pathlib import Path

import pytest
from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.domain import meal_affinity
from nutriplan.domain.models import DEFAULT_SLOT_WEIGHT, MealSlot

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "foods" / "curated_foods.csv"


@pytest.fixture(scope="module")
def foods():
    return catalog_by_name()


def test_eggs_are_breakfast_never_snack_and_never_main(foods) -> None:
    """El huevo duro NO es un snack. Es lo que salía cuando el snack tenía que
    aportar su cuota de proteína como cualquier otra comida."""
    huevo = foods["huevo entero"]
    clara = foods["clara de huevo"]
    yogur = foods["yogur griego natural"]
    assert meal_affinity.is_egg(huevo)
    assert meal_affinity.breakfast_protein(huevo)
    assert meal_affinity.breakfast_protein(yogur)
    assert meal_affinity.snack_protein(yogur)
    assert not meal_affinity.snack_protein(huevo)
    assert not meal_affinity.snack_protein(clara)
    # ni huevos ni lácteos son "plato principal" (esos son las carnes/pescados)
    assert not meal_affinity.main_protein(huevo)
    assert not meal_affinity.main_protein(yogur)


def test_meat_fish_seafood_are_main_not_breakfast_or_snack(foods) -> None:
    for name in ("pechuga de pollo", "salmón", "camarones", "carne de res magra"):
        f = foods[name]
        assert meal_affinity.main_protein(f), name
        assert not meal_affinity.breakfast_protein(f), name
        assert not meal_affinity.snack_protein(f), name


def test_breakfast_vs_main_carbs(foods) -> None:
    # avena/pan/arepa son de desayuno; arroz/pasta/papa son de plato principal
    assert meal_affinity.is_breakfast_carb(foods["avena en hojuelas"])
    assert meal_affinity.is_breakfast_carb(foods["arepa de maíz"])
    assert not meal_affinity.is_breakfast_carb(foods["arroz blanco cocido"])
    # La avena no va en almuerzo ni cena. El pan y la arepa SÍ pueden: quien solo
    # tiene arepa tiene que poder almorzar. Lo que decide es el peso, no el permiso.
    assert not meal_affinity.is_main_carb(foods["avena en hojuelas"])
    assert meal_affinity.is_main_carb(foods["arroz blanco cocido"])
    assert meal_affinity.is_main_carb(foods["arepa de maíz"])


def test_the_weight_says_whose_meal_it_is(foods) -> None:
    """La afinidad ordena: el arroz ES el almuerzo, la arepa solo cabe en él.

    Antes esto era un sí/no y el motor tomaba arroz y arepa por equivalentes en un
    almuerzo. De ahí salía la arepa a mediodía y el pan en la cena.
    """
    arroz, arepa, pan = (
        foods["arroz blanco cocido"], foods["arepa de maíz"], foods["pan integral"]
    )
    for main in (MealSlot.LUNCH, MealSlot.DINNER):
        assert arroz.weight_in(main) > arepa.weight_in(main)
        assert arroz.weight_in(main) > pan.weight_in(main)
    # Y al revés en el desayuno, que es de donde son.
    assert arepa.weight_in(MealSlot.BREAKFAST) > arepa.weight_in(MealSlot.LUNCH)
    assert pan.weight_in(MealSlot.BREAKFAST) > pan.weight_in(MealSlot.DINNER)
    # El arroz no desayuna: no es que pese poco, es que no está.
    assert arroz.weight_in(MealSlot.BREAKFAST) == 0

    # Un alimento que no declara pesos vale lo de siempre — el catálogo entero se
    # comportaba así antes de que esto existiera.
    assert foods["lentejas cocidas"].weight_in(MealSlot.LUNCH) == DEFAULT_SLOT_WEIGHT
