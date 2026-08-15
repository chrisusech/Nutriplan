"""Resumen de la semana al cerrar: kcal marcadas, veces fuera, veredicto."""

from tests.fixtures.plan_builder import build_fixed_plan

from nutriplan.application.eating_out import make_eating_out_entry
from nutriplan.domain.models import MacroTargets, MealSlot
from nutriplan.domain.restaurant import RestaurantDish
from nutriplan.domain.week_recap import week_recap

DAILY = MacroTargets(kcal=2000, protein_g=120, carb_g=200, fat_g=70)


def test_el_cierre_cuenta_las_kcal_marcadas_y_las_veces_fuera() -> None:
    plan, _foods, _ = build_fixed_plan()
    lunes = next(d for d in plan.days if d.day_index == 0)
    pizza = make_eating_out_entry(
        slot=MealSlot.DINNER,
        restaurant_id="dominos",
        restaurant_name="Domino's",
        dish=RestaurantDish(id="pepperoni", name="Pizza", protein_g=12, carb_g=35, fat_g=15),
    )
    comidas = [
        m.model_copy(update={"eaten": True}) if m.slot is not MealSlot.DINNER else pizza
        for m in lunes.meals
    ]
    dia = lunes.model_copy(update={"meals": comidas})
    semana = plan.model_copy(update={"days": [dia]})
    recap = week_recap(semana, DAILY)
    marked = [m for m in comidas if m.eaten]
    assert recap.kcal_eaten == round(sum(m.computed.kcal for m in marked))
    assert recap.kcal_target == 14000
    assert recap.eating_out_n == 1
    assert recap.out_line == "Comiste fuera 1 vez"
    assert "kcal esta semana" in recap.kcal_line


def test_con_pocas_comidas_marcadas_no_dice_que_fallo_el_objetivo() -> None:
    plan, _foods, _ = build_fixed_plan()
    recap = week_recap(plan, DAILY)
    assert recap.verdict == "partial"
    assert "parcial" in recap.verdict_line
    assert recap.out_line == "No comiste fuera"
