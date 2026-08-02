"""Tests de build_selection_prompt con hábitos del cliente."""

from datetime import UTC, datetime
from uuid import uuid4

from nutriplan.application.generate_plan import build_selection_prompt
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    NutritionTargets,
)
from nutriplan.domain.nutrition_config import NutritionConfig


def _targets() -> NutritionTargets:
    return NutritionTargets(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        daily=MacroTargets(kcal=1500, protein_g=100, carb_g=150, fat_g=50),
        per_meal={},
        method="test",
        config_version="v1",
        computed_at=datetime.now(UTC),
    )


def test_build_selection_prompt_includes_habits(nutrition_config: NutritionConfig) -> None:
    food = FoodItem(
        id=uuid4(),
        source="USDA",
        name_es="huevo entero",
        category=FoodCategory.PROTEIN,
        kcal_100g=143,
        protein_100g=12.6,
        carb_100g=0.7,
        fat_100g=9.5,
    )
    config = nutrition_config
    targets = _targets()
    prompt = build_selection_prompt(
        targets,
        [food],
        config,
        habits="Desayuno arepa con huevo; cena liviana.",
    )
    assert "HÁBITOS DEL CLIENTE" in prompt
    assert "arepa con huevo" in prompt


def test_build_selection_prompt_omits_empty_habits(nutrition_config: NutritionConfig) -> None:
    food = FoodItem(
        id=uuid4(),
        source="USDA",
        name_es="huevo entero",
        category=FoodCategory.PROTEIN,
        kcal_100g=143,
        protein_100g=12.6,
        carb_100g=0.7,
        fat_100g=9.5,
    )
    config = nutrition_config
    targets = _targets()
    prompt = build_selection_prompt(targets, [food], config, habits="  ")
    assert "HÁBITOS DEL CLIENTE" not in prompt
