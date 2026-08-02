"""Capa de IA: prompts versionados y contrato del mock (sin llamadas reales)."""

from pathlib import Path

import pytest

from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import PlanSelection

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

def test_los_prompts_estan_versionados_y_dicen_lo_que_la_ia_no_puede_hacer() -> None:
    plan = load_prompt(PROMPTS_DIR, "plan_generation", 1)
    assert plan.version == "plan_generation.v1"
    assert "Nunca decides cantidades" in plan.text


def test_pedir_una_version_de_prompt_que_no_existe_falla_ruidosamente() -> None:
    with pytest.raises(FileNotFoundError):
        load_prompt(PROMPTS_DIR, "plan_generation", 99)


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


async def test_una_respuesta_que_nunca_llego_es_un_error_y_no_un_plan_vacio() -> None:
    mock = MockLLMClient()
    with pytest.raises(LLMError):
        await mock.extract(system="s", text="t", schema=PlanSelection, model="m")
