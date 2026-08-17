"""La semana son 7 días desde que se genera el menú, no desde el lunes."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from nutriplan.domain.models import DayPlan, MacroTargets, PlanCycle, PlanStatus
from nutriplan.domain.week import (
    last_plan_day,
    next_week_start,
    plan_day_index,
    plan_is_live,
)
from nutriplan.ui.web.gate import plan_mutable
from nutriplan.ui.web.week_view import day_chips

_TOTALES = MacroTargets(kcal=2000, protein_g=150, carb_g=200, fat_g=60)


def _plan(week_start: date) -> PlanCycle:
    return PlanCycle(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        targets_id=uuid4(),
        status=PlanStatus.DRAFT,
        config_version="1",
        prompt_version="1",
        model="m",
        input_hash="h",
        created_at=datetime.now(UTC),
        week_start=week_start,
        days=[DayPlan(day_index=i, meals=[], totals=_TOTALES) for i in range(7)],
    )


def test_quien_se_registra_un_sabado_recibe_siete_dias() -> None:
    sabado = date(2026, 8, 15)
    assert plan_is_live(sabado, sabado)
    assert plan_day_index(sabado, sabado) == 0
    assert plan_day_index(sabado, date(2026, 8, 21)) == 6
    assert plan_is_live(sabado, date(2026, 8, 21))
    assert not plan_is_live(sabado, date(2026, 8, 22))
    assert next_week_start(sabado) == date(2026, 8, 22)


def test_los_chips_de_un_sabado_dicen_sabado_a_viernes() -> None:
    sabado = date(2026, 8, 15)
    chips = day_chips(_plan(sabado), selected=0, today=sabado)
    assert [c["short"] for c in chips] == ["Sáb", "Dom", "Lun", "Mar", "Mié", "Jue", "Vie"]
    assert chips[0]["is_today"] is True
    assert chips[0]["label"] == "Sábado"
    assert chips[6]["label"] == "Viernes"


def test_si_la_tira_ya_vencio_se_abre_el_ultimo_dia() -> None:
    sabado = date(2026, 8, 15)
    assert last_plan_day(sabado, date(2026, 8, 22)) == 6
    assert last_plan_day(sabado, sabado) == 0


def test_un_plan_vencido_ya_no_es_mutable() -> None:
    sabado = date(2026, 8, 15)
    plan = _plan(sabado)
    assert plan_mutable(plan, sabado)
    assert plan_mutable(plan, sabado + timedelta(days=3))
    assert not plan_mutable(plan, sabado + timedelta(days=7))
    assert not plan_mutable(None, sabado)
