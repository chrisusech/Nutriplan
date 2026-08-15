"""Quién arma la semana: el motor de la casa o la IA."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.adapters.llm.heuristic import HeuristicSelector
from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.adapters.llm.offline_engine import build_offline_engine
from nutriplan.application.generate_plan import _generate_week
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    FoodCategory,
    FoodItem,
    Goal,
    MacroTargets,
    MealSlot,
    NutritionTargets,
    Sex,
)


def _targets() -> NutritionTargets:
    daily = MacroTargets(kcal=2000, protein_g=120, carb_g=200, fat_g=60)
    return NutritionTargets(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        daily=daily,
        per_meal={},
        config_version="t",
        computed_at=datetime.now(UTC),
    )


def _client() -> Client:
    return Client(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="Ana",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
    )


def _food() -> FoodItem:
    return FoodItem(
        id=uuid4(),
        source="curated",
        name_es="pollo",
        category=FoodCategory.PROTEIN,
        kcal_100g=110,
        protein_100g=23.0,
        carb_100g=0.0,
        fat_100g=2.0,
    )


def _despensa_completa() -> list[FoodItem]:
    """Un pool con proteína, carbo, fruta y verdura: el motor exige de todo."""
    foods = catalog_by_name()
    return [
        foods[n]
        for n in (
            "pechuga de pollo",
            "huevo entero",
            "atún en agua",
            "yogur griego natural",
            "arroz integral cocido",
            "arepa de maíz",
            "pan integral",
            "banano",
            "fresa",
            "aguacate",
            "aceite de oliva",
            "brócoli",
            "espinaca",
        )
    ]


def test_sin_catalogo_de_platos_el_motor_es_el_heuristico(nutrition_config) -> None:
    motor = build_offline_engine(
        allowed=[_food()], daily=_targets().daily, config=nutrition_config, seed=0
    )
    assert isinstance(motor, HeuristicSelector)


async def test_sin_select_foods_manda_el_motor_aunque_haya_llm(nutrition_config) -> None:
    """La IA cuesta dinero: si no se le pide elegir, no se le llama."""
    llamado = {"ia": False}

    class _IaQueNoDeberiaEntrar(MockLLMClient):
        async def select_plan(self, **kwargs: object) -> object:
            llamado["ia"] = True
            return await super().select_plan(**kwargs)  # type: ignore[arg-type]

    _, _, modelo = await _generate_week(
        client=_client(),
        targets=_targets(),
        allowed=_despensa_completa(),
        config=nutrition_config.for_slots([MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER]),
        llm=_IaQueNoDeberiaEntrar(),
        offline_engine=build_offline_engine,
        prompts_dir=Path("prompts"),
        model="gpt-test",
        variant=0,
        feedback=None,
        select_foods=False,
    )

    assert modelo == "engine-v1"
    assert not llamado["ia"]
