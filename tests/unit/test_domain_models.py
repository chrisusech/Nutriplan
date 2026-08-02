"""Invariantes de los modelos de dominio."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    DayPlan,
    FoodCategory,
    FoodItem,
    Goal,
    MacroTargets,
    MealEntry,
    MealFoodPortion,
    MealSlot,
    PlanCycle,
    PlanStatus,
    Recipe,
    Sex,
)


def make_client(**overrides) -> Client:
    base = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="Valeria Test",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
    )
    base.update(overrides)
    return Client(**base)


def test_client_valid() -> None:
    client = make_client()
    assert client.goal == Goal.LOSE_FAT
    assert client.restrictions == []


def test_client_rejects_nonpositive_measures() -> None:
    with pytest.raises(ValidationError):
        make_client(height_cm=0)
    with pytest.raises(ValidationError):
        make_client(weight_kg=-5)


def test_food_item_rejects_negative_macros() -> None:
    with pytest.raises(ValidationError):
        FoodItem(
            id=uuid4(),
            source="USDA",
            name_es="pollo",
            category=FoodCategory.PROTEIN,
            kcal_100g=-1,
            protein_100g=31,
            carb_100g=0,
            fat_100g=3.6,
        )


def test_portion_requires_positive_grams() -> None:
    with pytest.raises(ValidationError):
        MealFoodPortion(food_id=uuid4(), grams=0)


def test_day_plan_index_bounds() -> None:
    macro = MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0)
    with pytest.raises(ValidationError):
        DayPlan(day_index=7, meals=[], totals=macro)


def test_plan_cycle_roundtrip_serialization() -> None:
    macro = MacroTargets(kcal=500, protein_g=40, carb_g=50, fat_g=15)
    meal = MealEntry(
        slot=MealSlot.LUNCH,
        portions=[MealFoodPortion(food_id=uuid4(), grams=120)],
        computed=macro,
        free_salad=True,
    )
    plan = PlanCycle(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        targets_id=uuid4(),
        days=[DayPlan(day_index=0, meals=[meal], totals=macro)],
        config_version="2026.07.01",
        prompt_version="plan_generation.v1",
        model="claude-sonnet-5",
        input_hash="abc123",
        created_at=datetime.now(UTC),
    )
    assert plan.status == PlanStatus.DRAFT
    restored = PlanCycle.model_validate_json(plan.model_dump_json())
    assert restored == plan


def test_a_recipe_can_declare_its_macros_instead_of_its_ingredients() -> None:
    """El plato de restaurante: sin ingredientes, con sus macros exactos."""
    dish = Recipe(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="Hamburguesa del restaurante",
        macros=MacroTargets(kcal=550, protein_g=42, carb_g=55, fat_g=18),
        total_grams=350,
        meal_slots=[MealSlot.LUNCH, MealSlot.DINNER],
        created_at=datetime.now(UTC),
    )
    assert dish.ingredients == []
    assert dish.meal_slots == [MealSlot.LUNCH, MealSlot.DINNER]


def test_a_recipe_with_neither_ingredients_nor_macros_is_nothing() -> None:
    """Un plato que no aporta nada no puede entrar en un plan: mejor no nacer."""
    with pytest.raises(ValidationError):
        Recipe(
            id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            name="Aire",
            macros=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
            total_grams=100,
            created_at=datetime.now(UTC),
        )
