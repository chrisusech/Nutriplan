"""Módulo 4 (dominio): schema dinámico, solver, validación, estructura, variedad."""

import json

import pytest
from pydantic import ValidationError
from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.domain.errors import GenerationError
from nutriplan.domain.generation_rules import (
    check_variety,
    validate_selection_structure,
)
from nutriplan.domain.models import (
    MacroTargets,
    MealSlot,
    PlanSelection,
)
from nutriplan.domain.portioning import solve_day_portions
from nutriplan.domain.selection_schema import build_selection_schema
from nutriplan.domain.validation import validate_day

DAILY = MacroTargets(kcal=1703.5, protein_g=124.0, carb_g=182.6, fat_g=53.0)


@pytest.fixture(scope="module")
def foods():
    return catalog_by_name()


def day_meals(foods):
    return [
        (MealSlot.BREAKFAST, [foods["huevo entero"], foods["arepa de maíz"], foods["aguacate"]]),
        (MealSlot.SNACK_AM, [foods["yogur griego natural"], foods["banano"]]),
        (MealSlot.LUNCH, [foods["pechuga de pollo"], foods["arroz blanco cocido"]]),
        (MealSlot.SNACK_PM, [foods["queso fresco"], foods["manzana"]]),
        (MealSlot.DINNER, [foods["tilapia"], foods["batata cocida"]]),
    ]


# --- Portion solver ---


def test_solver_squares_the_day(foods, nutrition_config) -> None:
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    assert [m.slot for m in solved] == list(MealSlot)

    totals_p = sum(m.computed.protein_g for m in solved)
    totals_c = sum(m.computed.carb_g for m in solved)
    totals_f = sum(m.computed.fat_g for m in solved)
    totals_kcal = sum(m.computed.kcal for m in solved)

    assert totals_p == pytest.approx(DAILY.protein_g, rel=0.10)
    assert totals_c == pytest.approx(DAILY.carb_g, rel=0.12)
    assert totals_f == pytest.approx(DAILY.fat_g, rel=0.12)
    assert totals_kcal == pytest.approx(DAILY.kcal, rel=0.08)


def test_solver_respects_rounding_and_minimums(foods, nutrition_config) -> None:
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    step = nutrition_config.portioning.grams_rounding
    for meal in solved:
        for portion in meal.portions:
            assert portion.grams % step == 0
            assert portion.grams >= nutrition_config.portioning.min_portion_g


def test_unit_foods_quantize_to_whole_or_half(foods, nutrition_config) -> None:
    """Nunca '5.5 huevos': los gramos de un alimento contable son múltiplo
    exacto de su unidad (entero para huevo, medio para aguacate)."""
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    catalog = {f.id: f for _slot, fs in day_meals(foods) for f in fs}
    for meal in solved:
        for portion in meal.portions:
            food = catalog[portion.food_id]
            if food.name_es == "huevo entero":  # whole, 50 g
                assert portion.grams % 50 == 0, portion.grams
            if food.name_es == "aguacate":  # half, 50 g → múltiplos de 25
                assert portion.grams % 25 == 0, portion.grams


def test_solver_computed_is_recalculated_from_grams(foods, nutrition_config) -> None:
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    lookup = {f.id: f for f in foods.values()}
    for meal in solved:
        expected_p = round(
            sum(lookup[p.food_id].protein_100g * p.grams / 100 for p in meal.portions), 1
        )
        assert meal.computed.protein_g == expected_p


def test_solver_rejects_source_without_macro(foods, nutrition_config) -> None:
    # aceite (0 g de carbo) como única "fuente" de carbo del slot → error claro
    meals = [(MealSlot.LUNCH, [foods["aceite de oliva"]])] + [
        (slot, items) for slot, items in day_meals(foods) if slot != MealSlot.LUNCH
    ]
    with pytest.raises(GenerationError):
        solve_day_portions(meals, DAILY, nutrition_config)


# --- Validación de tolerancias ---


def test_validate_day_accepts_solved_day(foods, nutrition_config) -> None:
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    assert validate_day(solved, DAILY, nutrition_config) == []


def test_validate_day_flags_deviation(foods, nutrition_config) -> None:
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    inflated = DAILY.model_copy(update={"protein_g": DAILY.protein_g * 2})
    deviations = validate_day(solved, inflated, nutrition_config)
    assert any(d.macro == "protein_g" and d.scope == "day" for d in deviations)


# --- Schema dinámico (enum de ids permitidos) ---


def test_selection_schema_embeds_enum_and_rejects_foreign_ids(foods) -> None:
    allowed = [foods["pechuga de pollo"], foods["arroz blanco cocido"]]
    schema = build_selection_schema(allowed)

    json_schema = json.dumps(schema.model_json_schema())
    assert str(allowed[0].id) in json_schema  # el enum vive en el schema

    good_meal = {"slot": "almuerzo", "food_ids": [str(allowed[0].id)]}
    days = [{"day_index": i, "meals": [good_meal] * 5} for i in range(7)]
    schema.model_validate({"days": days})  # ids permitidos → ok

    bad_meal = {"slot": "almuerzo", "food_ids": ["00000000-0000-0000-0000-000000000000"]}
    bad_days = [{"day_index": i, "meals": [bad_meal] * 5} for i in range(7)]
    with pytest.raises(ValidationError):
        schema.model_validate({"days": bad_days})


def test_selection_schema_requires_allowed_foods() -> None:
    with pytest.raises(ValueError):
        build_selection_schema([])


# --- Estructura y variedad ---


def _selection(days: list[dict]) -> PlanSelection:
    return PlanSelection.model_validate({"days": days})


def _full_day(foods, i: int) -> dict:
    ids = {
        "desayuno": [foods["huevo entero"].id, foods["arepa de maíz"].id],
        "snack_am": [foods["yogur griego natural"].id, foods["banano"].id],
        "almuerzo": [foods["pechuga de pollo"].id, foods["arroz blanco cocido"].id],
        "snack_pm": [foods["queso fresco"].id, foods["manzana"].id],
        "cena": [foods["tilapia"].id, foods["batata cocida"].id],
    }
    return {
        "day_index": i,
        "meals": [
            {"slot": slot, "food_ids": [str(x) for x in food_ids]}
            for slot, food_ids in ids.items()
        ],
    }


def test_structure_accepts_valid_selection(foods) -> None:
    selection = _selection([_full_day(foods, i) for i in range(7)])
    lookup = {str(f.id): f for f in foods.values()}
    assert validate_selection_structure(selection, lookup) == []


def test_structure_flags_violations(foods) -> None:
    lookup = {str(f.id): f for f in foods.values()}
    day = _full_day(foods, 0)
    day["meals"][0]["food_ids"] = [str(foods["arepa de maíz"].id)]  # sin proteína
    day["meals"][1]["food_ids"] = [
        str(foods["yogur griego natural"].id),
        str(foods["aguacate"].id),  # grasa en snack
    ]
    day["meals"][2]["food_ids"] = [
        str(foods["pechuga de pollo"].id),
        str(foods["brócoli"].id),  # verdura porcionada
    ]
    selection = _selection([day] + [_full_day(foods, i) for i in range(1, 7)])
    reasons = {v.reason for v in validate_selection_structure(selection, lookup)}
    assert "falta fuente de proteína" in reasons
    assert "grasa no permitida en snack" in reasons
    assert any("ensalada libre" in r for r in reasons)
    assert any("falta" in r and "carbohidrato" in r for r in reasons)


def test_structure_flags_missing_slot(foods) -> None:
    lookup = {str(f.id): f for f in foods.values()}
    day = _full_day(foods, 0)
    day["meals"] = day["meals"][:4]  # sin cena
    selection = _selection([day] + [_full_day(foods, i) for i in range(1, 7)])
    assert any(
        v.reason == "slot faltante" and v.slot == MealSlot.DINNER
        for v in validate_selection_structure(selection, lookup)
    )


def test_variety_flags_overuse_when_alternatives_exist(foods) -> None:
    """Con opciones disponibles, repetir de más se marca (yogur 14× era esto)."""
    lookup = {str(f.id): f for f in foods.values()}
    days = []
    for i in range(7):
        d = _full_day(foods, i)
        # el almuerzo usa pollo 6 días y tilapia 1 → 2 opciones distintas
        protein = "tilapia" if i == 6 else "pechuga de pollo"
        d["meals"][2]["food_ids"] = [str(foods[protein].id), str(foods["arroz blanco cocido"].id)]
        days.append(d)
    violations = check_variety(_selection(days), lookup, max_protein_repeats=3, max_carb_repeats=4)
    # 2 proteínas → límite ⌈7/2⌉=4 (o el tope 3, el mayor); pollo 6× lo supera
    assert any(v.food_name == "pechuga de pollo" and v.times_used == 6 for v in violations)


def test_variety_relaxes_when_only_one_option(foods) -> None:
    """Lista pobre: una sola proteína viable no debe fallar la generación."""
    lookup = {str(f.id): f for f in foods.values()}
    # pollo en los 7 almuerzos, pero es la única opción usada en ese slot
    selection = _selection([_full_day(foods, i) for i in range(7)])
    violations = check_variety(selection, lookup, max_protein_repeats=3, max_carb_repeats=4)
    # límite adaptativo ⌈7/1⌉=7 → pollo 7× no se marca (nada mejor era posible)
    assert not any(v.food_name == "pechuga de pollo" for v in violations)


def test_variety_rotated_week_is_clean(foods) -> None:
    lookup = {str(f.id): f for f in foods.values()}
    days = []
    for i in range(7):
        d = _full_day(foods, i)
        proteins = ["pechuga de pollo", "tilapia", "carne de res magra"]
        carbs = ["arroz blanco cocido", "papa cocida", "quinoa cocida", "batata cocida"]
        d["meals"][2]["food_ids"] = [str(foods[proteins[i % 3]].id), str(foods[carbs[i % 4]].id)]
        d["meals"][4]["food_ids"] = [
            str(foods[proteins[(i + 1) % 3]].id), str(foods[carbs[(i + 1) % 4]].id)
        ]
        days.append(d)
    assert check_variety(_selection(days), lookup, max_protein_repeats=3, max_carb_repeats=4) == []
