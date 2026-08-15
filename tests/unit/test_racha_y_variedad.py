"""Racha de días completos y no repetir el caldo de la semana pasada."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.adapters.llm.template_selector import TemplateSelector
from nutriplan.adapters.meals.template_store import load_meal_catalog
from nutriplan.application.recent_dishes import dishes_of_previous_week
from nutriplan.domain.dish_recipe import dish_key
from nutriplan.domain.models import (
    DayPlan,
    MacroTargets,
    MealEntry,
    MealSlot,
    PlanCycle,
)
from nutriplan.domain.streak import current_streak, day_is_complete
from nutriplan.domain.swap_note import has_technique

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ROOT / "data" / "meals" / "food_classes.yaml"
TEMPLATES = ROOT / "data" / "meals" / "meal_templates.yaml"
DAILY = MacroTargets(kcal=2200, protein_g=150.0, carb_g=230.0, fat_g=70.0)


def _meal(*, eaten: bool) -> MealEntry:
    return MealEntry(
        slot=MealSlot.LUNCH,
        dish_name="Guiso criollo",
        template_id="guiso_criollo",
        dish_key="guiso-pasado",
        computed=MacroTargets(kcal=500, protein_g=40, carb_g=40, fat_g=15),
        eaten=eaten,
    )


_CERO = MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0)


def _day(index: int, *, eaten: bool) -> DayPlan:
    return DayPlan(day_index=index, meals=[_meal(eaten=eaten)], totals=_CERO)


def _plan(week: date, days: list[DayPlan]) -> PlanCycle:
    return PlanCycle(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        targets_id=uuid4(),
        week_start=week,
        days=days,
        config_version="t",
        prompt_version="t",
        model="engine-v1",
        input_hash="x",
        created_at=datetime(week.year, week.month, week.day, tzinfo=UTC),
    )


def test_un_dia_sin_todas_las_comidas_no_cuenta() -> None:
    day = DayPlan(
        day_index=0,
        meals=[_meal(eaten=True), _meal(eaten=False)],
        totals=_CERO,
    )
    assert day_is_complete(day) is False


def test_la_racha_cuenta_dias_seguidos_y_no_se_rompe_si_hoy_sigue_abierto() -> None:
    lunes = date(2026, 8, 10)
    days = [_day(i, eaten=i < 3) for i in range(7)]
    plan = _plan(lunes, days)
    # Hoy es jueves 13: lun-mar-mie cerrados, jueves abierto → racha 3.
    assert current_streak([plan], today=lunes + timedelta(days=3)) == 3


def test_la_semana_pasada_aporta_los_platos_a_penalizar() -> None:
    anterior = date(2026, 8, 3)
    actual = date(2026, 8, 10)
    viejo = _plan(anterior, [_day(0, eaten=True)])
    keys, templates = dishes_of_previous_week([viejo], actual)
    assert "guiso-pasado" in keys
    assert "guiso_criollo" in templates


def test_sudado_es_una_tecnica() -> None:
    assert has_technique("pollo sudado con papa")
    assert not has_technique("pollo con papa")


def test_un_plato_de_la_semana_pasada_no_gana_si_hay_alternativa() -> None:
    """El caldo de la semana pasada pierde contra otro plato que sí cuadra."""
    foods = list(catalog_by_name().values())
    catalog = load_meal_catalog(CLASSES, TEMPLATES)
    base = TemplateSelector(foods, catalog, DAILY, seed=0)
    week = base.select_week(seed=0)
    primero = week[0][MealSlot.LUNCH]
    key = dish_key(primero.template_id, list(primero.food_ids))
    evitado = TemplateSelector(
        foods,
        catalog,
        DAILY,
        seed=0,
        recent_keys=frozenset({key}),
        recent_templates=frozenset({primero.template_id}),
    )
    otro = evitado.select_week(seed=0)[0][MealSlot.LUNCH]
    assert dish_key(otro.template_id, list(otro.food_ids)) != key
