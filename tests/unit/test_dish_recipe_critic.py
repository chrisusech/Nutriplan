"""El crítico de plato: soft reject no tumba el menú."""

from uuid import uuid4

import pytest

from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.application import dish_recipes as recipes_mod
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
)


def _food(name: str = "Pollo", cat: FoodCategory = FoodCategory.PROTEIN) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        source="curated",
        name_es=name,
        category=cat,
        kcal_100g=100,
        protein_100g=20.0,
        carb_100g=0.0,
        fat_100g=2.0,
    )


@pytest.mark.asyncio
async def test_si_el_critico_rechaza_el_plato_no_se_guarda_receta() -> None:
    food = _food()
    meal = MealEntry(
        slot=MealSlot.BREAKFAST,
        dish_name="Pollo a la plancha",
        template_id="proteina_carbo_ensalada",
        dish_key="abc123",
        items=[MealItem(food_id=food.id, grams=120, position=0)],
        computed=MacroTargets(kcal=120, protein_g=24, carb_g=0, fat_g=2, fiber_g=0),
    )
    llm = MockLLMClient()
    llm.enqueue(
        {
            "adequacy": "reject",
            "issue_codes": ["slot_mismatch"],
            "critique_reason": "pollo de cena en el desayuno",
            "steps": ["Calienta la sartén.", "Cocina el pollo."],
            "prep_minutes": 15,
            "difficulty": "fácil",
            "tips": "",
        }
    )

    out = await recipes_mod._from_llm(
        "abc123",
        meal,
        {food.id: food},
        llm,
        "system",
        "test-model",
    )
    assert out is None


@pytest.mark.asyncio
async def test_si_el_critico_avisa_la_receta_sigue_con_el_pero() -> None:
    food = _food("Avena", FoodCategory.CARB)
    meal = MealEntry(
        slot=MealSlot.BREAKFAST,
        dish_name="Avena",
        template_id="bowl_lacteo_avena_fruta",
        dish_key="avena1",
        items=[MealItem(food_id=food.id, grams=40, position=0)],
        computed=MacroTargets(kcal=80, protein_g=3, carb_g=14, fat_g=1, fiber_g=2),
    )
    llm = MockLLMClient()
    llm.enqueue(
        {
            "adequacy": "warn",
            "issue_codes": ["prep_heavy"],
            "critique_reason": "Tarda un poco si vas con prisa.",
            "name_es": "Avena cremosa al vapor",
            "steps": ["Hierve agua.", "Cocina la avena."],
            "prep_minutes": 10,
            "difficulty": "fácil",
            "tips": "Remueve sin parar.",
        }
    )

    out = await recipes_mod._from_llm(
        "avena1",
        meal,
        {food.id: food},
        llm,
        "system",
        "test-model",
    )
    assert out is not None
    assert out.source == "ai"
    assert "prisa" in (out.tips or "").lower()
