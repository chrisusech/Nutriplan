"""Afinidad por comida: qué alimento va bien en cada slot (calidad culinaria)."""

from pathlib import Path

import pytest
from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.domain import meal_affinity

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
    # avena/pan NO van en almuerzo/cena; arroz/papa/arepa sí
    assert not meal_affinity.is_main_carb(foods["avena en hojuelas"])
    assert not meal_affinity.is_main_carb(foods["pan integral"])
    assert meal_affinity.is_main_carb(foods["arroz blanco cocido"])
    assert meal_affinity.is_main_carb(foods["arepa de maíz"])  # sirve en ambos
