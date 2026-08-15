"""El historial cruza peso, kcal comidas y el día a día de la semana."""

from datetime import UTC, date, datetime
from uuid import uuid4

from tests.fixtures.plan_builder import build_fixed_plan

from nutriplan.application.progress import build_progress, daily_kcal
from nutriplan.domain.models import MacroFormula, MacroTargets, NutritionTargets, WeightEntry

DAILY = MacroTargets(kcal=2000, protein_g=120, carb_g=200, fat_g=70)
WEEK = date(2026, 8, 10)


def test_el_progreso_cruza_kcal_comidas_con_el_objetivo() -> None:
    plan, _foods, _ = build_fixed_plan()
    lunes = next(d for d in plan.days if d.day_index == 0)
    comidas = [m.model_copy(update={"eaten": True}) for m in lunes.meals]
    plan = plan.model_copy(
        update={"week_start": WEEK, "days": [lunes.model_copy(update={"meals": comidas})]}
    )
    entry = WeightEntry(
        id=uuid4(),
        tenant_id=plan.tenant_id,
        client_id=plan.client_id,
        weight_kg=62.0,
        week_start=WEEK,
        logged_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    targets = NutritionTargets(
        id=plan.targets_id,
        tenant_id=plan.tenant_id,
        client_id=plan.client_id,
        daily=DAILY,
        per_meal={},
        config_version="t",
        formula=MacroFormula(),
        weight_kg=62.0,
        computed_at=datetime(2026, 8, 10, tzinfo=UTC),
    )
    weeks = build_progress(entries=[entry], targets=[targets], plans=[plan])
    assert len(weeks) == 1
    assert weeks[0].kcal == 2000
    assert weeks[0].kcal_target == 14000
    assert weeks[0].kcal_eaten == round(sum(m.computed.kcal for m in comidas))

    dias = daily_kcal(plan, DAILY)
    assert [d.label for d in dias] == ["L"]
    assert dias[0].kcal == weeks[0].kcal_eaten
    assert dias[0].objetivo == 2000
