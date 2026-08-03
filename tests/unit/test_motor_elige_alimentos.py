"""El motor elige; la IA no entra en select_plan en la beta."""

from datetime import UTC, datetime
from uuid import uuid4

from nutriplan.adapters.llm.heuristic import HeuristicSelector
from nutriplan.adapters.llm.mock_client import MockLLMClient
from nutriplan.application.generate_plan import _selector_for
from nutriplan.domain.models import MacroTargets, NutritionTargets


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


def test_sin_select_foods_se_usa_el_heuristico_aunque_haya_llm(nutrition_config) -> None:
    llm = MockLLMClient()
    selector = _selector_for(
        llm, [], _targets(), nutrition_config, variant=0, catalog=None, select_foods=False,
    )
    assert isinstance(selector, HeuristicSelector)


def test_con_select_foods_el_llm_manda(nutrition_config) -> None:
    llm = MockLLMClient()
    selector = _selector_for(
        llm, [], _targets(), nutrition_config, variant=0, catalog=None, select_foods=True,
    )
    assert selector is llm
