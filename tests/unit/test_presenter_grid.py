"""Presenter: rejilla de revisión por fase."""

from datetime import UTC, datetime
from uuid import uuid4

from tests.fixtures.plan_builder import build_fixed_plan

from nutriplan.domain.models import PlanPhase
from nutriplan.ui.web import presenter


def test_week_grid_filters_by_phase(nutrition_config) -> None:
    plan, _, _ = build_fixed_plan()
    # Simula plan 30 días: duplicar días con otra fase
    from nutriplan.domain.models import DayPlan

    second_week = [
        DayPlan(
            day_index=d.day_index,
            phase=PlanPhase.NEXT_15,
            meals=d.meals,
            totals=d.totals,
        )
        for d in plan.days
    ]
    plan = plan.model_copy(update={
        "duration_days": 30,
        "days": plan.days + second_week,
    })
    assert len(plan.days) == 14

    targets_id = uuid4()
    from nutriplan.domain.models import MacroTargets, NutritionTargets

    targets = NutritionTargets(
        id=targets_id,
        tenant_id=plan.tenant_id,
        client_id=plan.client_id,
        daily=MacroTargets(kcal=1700, protein_g=120, carb_g=180, fat_g=55),
        per_meal={},
        config_version="test",
        computed_at=datetime.now(UTC),
    )

    grid_w1 = presenter.week_grid(plan, targets, nutrition_config, PlanPhase.FIRST_15)
    grid_w2 = presenter.week_grid(plan, targets, nutrition_config, PlanPhase.NEXT_15)
    assert len(grid_w1) == 7
    assert len(grid_w2) == 7
    assert all(c["fase"] == PlanPhase.FIRST_15.value for c in grid_w1)
    assert all(c["fase"] == PlanPhase.NEXT_15.value for c in grid_w2)


def test_parse_plan_phase_fallback() -> None:
    assert presenter.parse_plan_phase("first_15").value == "first_15"
    assert presenter.parse_plan_phase("invalid").value == "first_15"
    assert presenter.parse_plan_phase(None).value == "first_15"
