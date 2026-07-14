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


def test_the_day_of_the_free_meal_is_not_painted_as_broken(nutrition_config) -> None:
    """El fallo más fácil de colar en todo esto.

    Ese día suma MENOS que el objetivo diario, a propósito. Si la pantalla lo juzga
    contra el objetivo completo lo pinta en naranja ("no cuadra"), y el entrenador
    ve roto un plan que está exactamente como se diseñó.

    El día se construye como lo construye la generación: porcionando las CUATRO
    comidas que quedan contra el objetivo reducido.
    """
    from tests.fixtures.plan_builder import catalog_by_name

    from nutriplan.domain.macro_split import daily_minus_free_meal, macro_shares
    from nutriplan.domain.models import (
        DayPlan,
        MacroTargets,
        MealEntry,
        MealItem,
        MealSlot,
        NutritionTargets,
    )
    from nutriplan.domain.portioning import solve_day_portions
    from nutriplan.domain.validation import day_totals

    foods = catalog_by_name()
    foods_map = {f.id: f for f in foods.values()}
    daily = MacroTargets(kcal=1800, protein_g=110, carb_g=190, fat_g=55, fiber_g=25)

    # El domingo la cena es libre: se porcionan las otras cuatro contra SU objetivo.
    reduced = daily_minus_free_meal(daily, nutrition_config, MealSlot.DINNER)
    assert reduced.kcal < daily.kcal

    meals_input = [
        (MealSlot.BREAKFAST, [foods["huevo entero"], foods["avena en hojuelas"]]),
        (MealSlot.SNACK_AM, [foods["banano"]]),
        (MealSlot.LUNCH, [foods["pechuga de pollo"], foods["arroz blanco cocido"],
                          foods["aguacate"]]),
        (MealSlot.SNACK_PM, [foods["fresa"]]),
    ]
    solved = solve_day_portions(meals_input, reduced, nutrition_config)

    meals = [
        MealEntry(
            slot=m.slot,
            items=[MealItem(food_id=p.food_id, grams=p.grams, position=i)
                   for i, p in enumerate(m.portions)],
            computed=m.computed,
        )
        for m in solved
    ]
    meals.append(MealEntry(
        slot=MealSlot.DINNER, items=[],
        computed=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
        is_free_meal=True,
    ))
    sunday = DayPlan(day_index=6, phase=PlanPhase.FIRST_15, meals=meals,
                     totals=day_totals(solved))

    plan, _, _ = build_fixed_plan()
    plan = plan.model_copy(update={"days": [*plan.days[:6], sunday]})
    targets = NutritionTargets(
        id=uuid4(), tenant_id=plan.tenant_id, client_id=plan.client_id,
        daily=daily, per_meal={}, config_version="test", computed_at=datetime.now(UTC),
    )

    # Contra el objetivo completo, este día "no cuadra" — es la trampa.
    from nutriplan.domain.validation import validate_day
    shares = macro_shares([(s, f) for s, f in meals_input], nutrition_config)
    assert validate_day(meals, daily, nutrition_config, shares=shares), (
        "contra el objetivo diario el día parece roto: por eso hay que reducirlo"
    )

    # Y contra el suyo, cuadra. Es lo que la pantalla tiene que enseñar.
    grid = presenter.week_grid(plan, targets, nutrition_config, PlanPhase.FIRST_15,
                               foods=foods_map)
    assert grid[6]["fit"], "el día de la comida libre no está roto: apunta más bajo"

    dv = presenter.day_view(sunday, targets, nutrition_config, foods_map)
    assert dv.fits
    free_meal = next(m for m in dv.meals if m.is_free_meal)
    assert free_meal.slot is MealSlot.DINNER
    assert free_meal.chips == []
