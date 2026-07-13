"""Módulo 3: importador del CSV curado, filtro de restricciones y matching."""

from pathlib import Path

import pytest

from nutriplan.adapters.food.curated_loader import load_curated_foods
from nutriplan.domain.food_filter import KNOWN_TAGS, allowed_foods, forbidden_tags
from nutriplan.domain.food_matching import match_food_names, normalize
from nutriplan.domain.models import FoodCategory

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "foods" / "curated_foods.csv"


@pytest.fixture(scope="module")
def catalog():
    return load_curated_foods(CSV_PATH)


# --- Importador ---


def test_catalog_size_and_categories(catalog) -> None:
    assert len(catalog) >= 60
    # OTHER ya se usa: es la categoría de los alimentos libres (ensalada, café).
    assert {f.category for f in catalog} == set(FoodCategory)


def test_catalog_is_global_and_tagged_with_known_vocabulary(catalog) -> None:
    for food in catalog:
        assert food.tenant_id is None
        assert set(food.tags) <= KNOWN_TAGS, f"{food.name_es}: tags fuera de vocabulario"
        assert 0 <= food.kcal_100g <= 900
        assert food.protein_100g + food.carb_100g + food.fat_100g <= 105


def test_import_is_idempotent(catalog) -> None:
    again = load_curated_foods(CSV_PATH)
    assert [f.id for f in again] == [f.id for f in catalog]


def test_names_are_unique(catalog) -> None:
    names = [f.name_es for f in catalog]
    assert len(names) == len(set(names))


# --- Filtro de restricciones (conjunto permitido) ---


def test_no_seafood_removes_shrimp_only(catalog) -> None:
    allowed = allowed_foods(catalog, ["no_seafood"])
    removed = {f.name_es for f in catalog} - {f.name_es for f in allowed}
    assert removed == {"camarones"}


def test_no_dairy_removes_whey_and_dairy(catalog) -> None:
    allowed = allowed_foods(catalog, ["no_dairy"])
    names = {f.name_es for f in allowed}
    assert "yogur griego natural" not in names
    assert "proteína en polvo whey" not in names
    assert "pechuga de pollo" in names


def test_combined_restrictions(catalog) -> None:
    allowed = allowed_foods(catalog, ["no_fish", "no_gluten", "no_nuts"])
    names = {f.name_es for f in allowed}
    for banned in ("salmón", "atún en agua", "camarones", "pasta cocida", "almendras"):
        assert banned not in names
    assert "arroz blanco cocido" in names


def test_direct_tag_accepted_as_restriction(catalog) -> None:
    allowed = allowed_foods(catalog, ["gluten"])
    assert "pan integral" not in {f.name_es for f in allowed}


def test_unrecognized_restriction_is_flagged_not_guessed(catalog) -> None:
    tags, unrecognized = forbidden_tags(["no_seafood", "sin cilantro"])
    assert tags == {"mariscos"}
    assert unrecognized == ["sin cilantro"]
    with pytest.raises(ValueError):
        allowed_foods(catalog, ["sin cilantro"])


# --- Matching texto → FoodItem ---


def test_exact_match(catalog) -> None:
    result = match_food_names(["pechuga de pollo"], catalog)
    assert result.matched["pechuga de pollo"].name_es == "pechuga de pollo"
    assert result.unrecognized == []


def test_normalized_match_accents_and_case(catalog) -> None:
    result = match_food_names(["Brócoli", "PIÑA", "salmon"], catalog)
    assert result.matched["Brócoli"].name_es == "brócoli"
    assert result.matched["PIÑA"].name_es == "piña"
    assert result.matched["salmon"].name_es == "salmón"


def test_plural_match(catalog) -> None:
    result = match_food_names(["manzanas", "huevos"], catalog)
    assert result.matched["manzanas"].name_es == "manzana"
    assert result.matched["huevos"].name_es == "huevo entero"


def test_fuzzy_word_subset(catalog) -> None:
    result = match_food_names(["pollo", "arroz integral"], catalog)
    assert result.matched["pollo"].name_es == "pechuga de pollo"
    assert result.matched["arroz integral"].name_es == "arroz integral cocido"


def test_unknown_food_marked_not_guessed(catalog) -> None:
    result = match_food_names(["salchicha ranchera", "pollo"], catalog)
    assert result.unrecognized == ["salchicha ranchera"]
    assert "pollo" in result.matched


def test_normalize_examples() -> None:
    assert normalize("Brócoli") == "brocoli"
    assert normalize("MANZANAS  rojas") == "manzana roja"
