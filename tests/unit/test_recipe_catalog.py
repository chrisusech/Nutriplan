"""Catálogo curado gana a la IA; tokens de alimento resuelven nombres del plan."""

from uuid import uuid4

from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealFoodPortion,
    MealSlot,
    UnitGranularity,
)
from nutriplan.domain.recipe_catalog import (
    CuratedRecipe,
    food_matches_token,
    match_curated,
)


def _food(name: str, *aliases: str) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        name_es=name,
        category=FoodCategory.PROTEIN,
        kcal_100g=100,
        protein_100g=10,
        carb_100g=10,
        fat_100g=2,
        unit_granularity=UnitGranularity.GRAMS,
        aliases=list(aliases),
        source="test",
    )


def test_token_huevo_entero_pega_al_nombre_del_csv() -> None:
    food = _food("huevo entero", "huevo", "huevos")
    assert food_matches_token(food, "huevo_entero")


def test_token_arepa_pega_por_alias() -> None:
    food = _food("arepa de maíz", "arepa")
    assert food_matches_token(food, "arepa")


def test_la_receta_curada_cubre_un_plato_con_extras() -> None:
    """Aguacate de más en el desayuno no tumba el match huevo+arepa."""
    huevo = _food("huevo entero", "huevo")
    arepa = _food("arepa de maíz", "arepa")
    aguacate = _food("aguacate")
    meal = MealEntry(
        slot=MealSlot.BREAKFAST,
        portions=[
            MealFoodPortion(food_id=huevo.id, grams=100),
            MealFoodPortion(food_id=arepa.id, grams=70),
            MealFoodPortion(food_id=aguacate.id, grams=40),
        ],
        computed=MacroTargets(kcal=400, protein_g=20, carb_g=30, fat_g=15),
    )
    curated = CuratedRecipe(
        id="huevos_arepa",
        name_es="Huevos con arepa",
        foods=["huevo_entero", "arepa"],
        steps=["Bate.", "Sirve."],
        prep_minutes=10,
        difficulty="fácil",
    )
    catalog = {f.id: f for f in (huevo, arepa, aguacate)}
    assert match_curated(meal, catalog, [curated]) is curated


def test_sin_todos_los_alimentos_no_hay_match() -> None:
    pollo = _food("pechuga de pollo", "pollo")
    meal = MealEntry(
        slot=MealSlot.LUNCH,
        portions=[MealFoodPortion(food_id=pollo.id, grams=150)],
        computed=MacroTargets(kcal=250, protein_g=40, carb_g=0, fat_g=5),
    )
    curated = CuratedRecipe(
        id="pollo_arroz",
        name_es="Pollo con arroz",
        foods=["pechuga_pollo", "arroz_blanco"],
        steps=["Cocina."],
    )
    assert match_curated(meal, {pollo.id: pollo}, [curated]) is None
