"""Bordes del generador: fallbacks, sin alimentos, refine opcional."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from nutriplan.adapters.llm.heuristic import HeuristicSelector
from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.adapters.llm.template_selector import InsufficientDishes
from nutriplan.application.generate_plan import (
    _selector_for,
    generate_cycle,
    generate_plan_for_client,
)
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.meal_template import MealCatalog
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    DayPlan,
    FoodCategory,
    FoodItem,
    Goal,
    MacroTargets,
    MealEntry,
    MealSlot,
    NutritionTargets,
    PlanStatus,
    Sex,
)
from nutriplan.domain.nutrition_config import NutritionConfig


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


def _client(**extra: object) -> Client:
    data = dict(
        id=uuid4(),
        tenant_id=uuid4(),
        user_id=uuid4(),
        name="T",
        sex=Sex.FEMALE,
        age_years=28,
        height_cm=165,
        weight_kg=62,
        activity_level=ActivityLevel.MODERATE,
        goal=Goal.LOSE_FAT,
        meal_slots=[MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER],
    )
    data.update(extra)
    return Client(**data)  # type: ignore[arg-type]


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


def test_si_el_catalogo_no_alcanza_cae_al_heuristico(
    nutrition_config: NutritionConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise InsufficientDishes("sin platos")

    monkeypatch.setattr(
        "nutriplan.adapters.llm.template_selector.TemplateSelector.__init__",
        _boom,
    )
    catalog = MealCatalog(version="t", classes={}, templates=())
    selector = _selector_for(
        None, [_food()], _targets(), nutrition_config, 0, catalog, select_foods=False
    )
    assert isinstance(selector, HeuristicSelector)


@pytest.mark.asyncio
async def test_sin_alimentos_generate_plan_falla_claro(
    nutrition_config: NutritionConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "nutriplan.application.generate_plan.resolve_allowed_foods",
        AsyncMock(return_value=[]),
    )
    food_repo = AsyncMock()
    plan_repo = AsyncMock()
    client_repo = AsyncMock()
    with pytest.raises(GenerationError, match="alimento"):
        await generate_plan_for_client(
            client=_client(),
            targets=_targets(),
            food_repo=food_repo,
            plan_repo=plan_repo,
            client_repo=client_repo,
            config=nutrition_config,
            llm=None,
            prompts_dir=Path("prompts"),
            model="engine-v1",
        )


@pytest.mark.asyncio
async def test_con_refine_names_el_plan_queda_marcado(
    nutrition_config: NutritionConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER]
    macros = MacroTargets(kcal=400, protein_g=30, carb_g=40, fat_g=10)
    meals = [
        MealEntry(slot=s, computed=macros, dish_name=f"Plato {s.value}", items=[])
        for s in slots
    ]
    day_macros = MacroTargets(kcal=1200, protein_g=90, carb_g=120, fat_g=30)
    week = [
        DayPlan(day_index=i, meals=meals, totals=day_macros) for i in range(7)
    ]

    async def _ok_week(**_k: object) -> tuple[list[DayPlan], list[str]]:
        return week, []

    class _Refined:
        days = week

    async def _refine(**_k: object) -> _Refined:
        return _Refined()

    monkeypatch.setattr(
        "nutriplan.application.generate_plan._generate_week", _ok_week
    )
    monkeypatch.setattr(
        "nutriplan.application.generate_plan.refine_week", _refine
    )

    cycle = await generate_cycle(
        client=_client(meal_slots=slots),
        targets=_targets(),
        allowed=[_food()],
        config=nutrition_config.for_slots(slots),
        llm=MockLLMClient(),
        prompts_dir=Path("prompts"),
        model="gpt-test",
        select_foods=False,
        refine_names=True,
    )
    assert cycle.refined_at is not None
    assert cycle.refine_model == "gpt-test"
    assert cycle.status is PlanStatus.DRAFT
    assert cycle.model == "engine-v1"


@pytest.mark.asyncio
async def test_si_el_llm_falla_seleccionando_entra_el_motor(
    nutrition_config: NutritionConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con select_foods=True, un LLMError no deja a nadie sin menú."""
    from nutriplan.domain.errors import LLMError

    class _Falla:
        async def select_plan(self, **_k: object) -> object:
            raise LLMError("sin cuota")

        def pop_usage(self) -> dict[str, int]:
            return {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    saw_fallback = {"ok": False}

    class _FakeOffline:
        def __init__(self, *a: object, **k: object) -> None:
            pass

        async def select_plan(self, **_k: object) -> object:
            saw_fallback["ok"] = True
            raise LLMError("también falla el offline en este stub")

        def pop_usage(self) -> dict[str, int]:
            return {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    # HeuristicSelector se importa dentro de _selector_for / _generate_week.
    monkeypatch.setattr(
        "nutriplan.adapters.llm.heuristic.HeuristicSelector", _FakeOffline
    )
    from nutriplan.application import generate_plan as gp

    with pytest.raises(LLMError, match="offline"):
        await gp._generate_week(
            client=_client(),
            targets=_targets(),
            allowed=[_food()],
            config=nutrition_config.for_slots(
                [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER]
            ),
            llm=_Falla(),  # type: ignore[arg-type]
            prompts_dir=Path("prompts"),
            model="test",
            variant=0,
            feedback=None,
            catalog=None,
            select_foods=True,
        )
    assert saw_fallback["ok"]
