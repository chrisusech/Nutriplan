"""Capa de IA: prompts versionados y contrato del mock (sin llamadas reales)."""

from pathlib import Path

import pytest

from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import IntakeParsed, PlanSelection

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

INTAKE_FIXTURE = {
    "name": "Ana Pérez",
    "sex": "female",
    "age_years": 28,
    "height_cm": 165.0,
    "weight_kg": 62.0,
    "weight_is_approximate": False,
    "city": "Medellín",
    "goal_raw": "bajar grasa y tonificar",
    "liked_foods": {
        "proteins": ["pollo", "huevos"],
        "carbs": ["arroz", "arepa"],
        "fats": ["aguacate"],
        "fruits": ["banano"],
        "vegetables": ["brócoli"],
    },
    "restrictions_raw": ["sin mariscos"],
    "uses_protein_shake": False,
    "training_raw": "pesas 4 veces por semana",
}


def test_prompts_v1_exist_and_are_versioned() -> None:
    intake = load_prompt(PROMPTS_DIR, "intake_extraction", 1)
    plan = load_prompt(PROMPTS_DIR, "plan_generation", 1)
    assert intake.version == "intake_extraction.v1"
    assert plan.version == "plan_generation.v1"
    assert "no interpretas" in intake.text or "No interpretas".lower() in intake.text.lower()
    assert "Nunca decides cantidades" in plan.text


def test_missing_prompt_version_fails_loud() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt(PROMPTS_DIR, "intake_extraction", 99)


async def test_mock_extract_returns_validated_schema() -> None:
    mock = MockLLMClient()
    mock.enqueue(INTAKE_FIXTURE)
    result = await mock.extract(
        system="s", text="doc", schema=IntakeParsed, model="claude-haiku-4-5"
    )
    assert isinstance(result, IntakeParsed)
    assert result.name == "Ana Pérez"
    assert mock.calls[0]["schema"] == "IntakeParsed"


async def test_mock_rejects_payload_that_violates_schema() -> None:
    mock = MockLLMClient()
    mock.enqueue({**INTAKE_FIXTURE, "campo_extra": 1})  # extra="forbid"
    with pytest.raises(LLMError):
        await mock.extract(system="s", text="doc", schema=IntakeParsed, model="m")


async def test_mock_select_plan_roundtrip() -> None:
    payload = {
        "days": [
            {
                "day_index": i,
                "meals": [
                    {"slot": "desayuno", "food_ids": ["id-1"], "free_salad": False},
                    {"slot": "almuerzo", "food_ids": ["id-2"], "free_salad": True},
                ],
            }
            for i in range(7)
        ]
    }
    mock = MockLLMClient()
    mock.enqueue(payload)
    result = await mock.select_plan(
        system="s", prompt="p", schema=PlanSelection, model="claude-sonnet-5"
    )
    assert len(result.days) == 7
    assert result.days[0].meals[1].free_salad is True


async def test_mock_empty_queue_raises() -> None:
    mock = MockLLMClient()
    with pytest.raises(LLMError):
        await mock.extract(system="s", text="t", schema=IntakeParsed, model="m")
