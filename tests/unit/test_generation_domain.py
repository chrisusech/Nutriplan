"""Módulo 4 (dominio): schema dinámico, solver, validación, estructura, variedad."""

import json
from collections import Counter

import pytest
from pydantic import ValidationError
from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.domain.errors import GenerationError
from nutriplan.domain.generation_rules import (
    check_variety,
    drop_free_meal,
    slot_availability,
    validate_selection_structure,
)
from nutriplan.domain.macro_split import daily_minus_free_meal, macro_shares
from nutriplan.domain.models import (
    DaySelection,
    FoodCategory,
    MacroTargets,
    MealSelection,
    MealSlot,
    PlanSelection,
)
from nutriplan.domain.portioning import solve_day_portions
from nutriplan.domain.selection_schema import build_selection_schema
from nutriplan.domain.validation import fiber_shortfall, validate_day

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


def test_a_snack_of_only_fruit_squares_the_day(foods, nutrition_config) -> None:
    """Un snack puede ser una manzana y ya: la proteína la ponen las comidas grandes.

    Es el corazón del asunto. Antes cada comida debía su 10% de proteína, así que
    un snack sin fuente proteica dejaba el día corto y el plan se rechazaba — y por
    eso el motor metía huevo duro a media mañana. Ahora `macro_shares` reparte cada
    macro solo entre quien tiene fuente, y el desayuno, el almuerzo y la cena
    absorben lo que el snack no lleva.
    """
    meals = [
        (MealSlot.BREAKFAST, [foods["huevo entero"], foods["arepa de maíz"], foods["aguacate"]]),
        (MealSlot.SNACK_AM, [foods["manzana"]]),  # solo fruta
        (MealSlot.LUNCH, [foods["pechuga de pollo"], foods["arroz blanco cocido"]]),
        (MealSlot.SNACK_PM, [foods["banano"], foods["almendras"]]),  # fruta + grasa
        (MealSlot.DINNER, [foods["tilapia"], foods["batata cocida"]]),
    ]
    solved = solve_day_portions(meals, DAILY, nutrition_config)
    shares = macro_shares(meals, nutrition_config)

    # Ni el día ni ningún slot se sale de tolerancia...
    assert validate_day(solved, DAILY, nutrition_config, shares=shares) == []
    # ...y el snack no debe proteína que no puede dar.
    assert shares[MealSlot.SNACK_AM]["protein_g"] == 0.0
    # La cuota del snack no se pierde: se la reparten las comidas con fuente.
    assert sum(s["protein_g"] for s in shares.values()) == pytest.approx(1.0)
    assert sum(m.computed.protein_g for m in solved) == pytest.approx(DAILY.protein_g, rel=0.10)


def test_a_client_who_eats_four_meals_gets_four(foods, nutrition_config) -> None:
    """Quien come cuatro veces no recibe un plan de cinco.

    El reparto se renormaliza sobre las comidas que quedan (no se pierde el 7.5%
    del snack que no hace), y el día cuadra igual.
    """
    slots = [MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.SNACK_PM, MealSlot.DINNER]
    config = nutrition_config.for_slots(slots)
    assert list(config.meal_distribution) == slots
    assert sum(s.kcal for s in config.meal_distribution.values()) == pytest.approx(1.0)

    meals = [
        (MealSlot.BREAKFAST, [foods["huevo entero"], foods["arepa de maíz"], foods["aguacate"]]),
        (MealSlot.LUNCH, [foods["pechuga de pollo"], foods["arroz blanco cocido"]]),
        (MealSlot.SNACK_PM, [foods["yogur griego natural"], foods["manzana"]]),
        (MealSlot.DINNER, [foods["tilapia"], foods["batata cocida"]]),
    ]
    solved = solve_day_portions(meals, DAILY, config)
    assert [m.slot for m in solved] == slots
    assert validate_day(solved, DAILY, config, shares=macro_shares(meals, config)) == []
    assert sum(m.computed.kcal for m in solved) == pytest.approx(DAILY.kcal, rel=0.08)


def test_portions_land_on_numbers_a_person_can_weigh(foods, nutrition_config) -> None:
    """Cada porción cae en la rejilla de SU alimento, no en una global de 5 g.

    Es más fácil pesar 120 o 150 g que 137, y los huevos se cuentan de uno en
    uno. El paso lo declara el alimento; el piso también (una cucharada de
    aceite no baja de 10 g).
    """
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    catalog = {f.id: f for _slot, fs in day_meals(foods) for f in fs}
    for meal in solved:
        for portion in meal.portions:
            food = catalog[portion.food_id]
            step = food.portion_step_g
            floor = food.portion_min_g or nutrition_config.portioning.min_portion_g
            assert portion.grams % step == 0, f"{food.name_es}: {portion.grams} g no cae en {step}"
            assert portion.grams >= floor, f"{food.name_es}: {portion.grams} g bajo su piso"


def test_unit_foods_quantize_to_whole_or_half(foods, nutrition_config) -> None:
    """Nunca '5.5 huevos': un alimento contable (huevo entero) rinde gramos
    múltiplo exacto de su unidad. El aguacate ahora va por gramos libres."""
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    catalog = {f.id: f for _slot, fs in day_meals(foods) for f in fs}
    for meal in solved:
        for portion in meal.portions:
            food = catalog[portion.food_id]
            if food.name_es == "huevo entero":  # whole, 50 g
                assert portion.grams % 50 == 0, portion.grams
            if food.name_es == "aguacate":  # grams → múltiplos de 5 g
                assert portion.grams % 5 == 0, portion.grams


def test_tuna_portions_are_whole_cans(foods, nutrition_config) -> None:
    meals = [
        (MealSlot.LUNCH, [foods["atún en agua"], foods["arroz blanco cocido"]]),
    ] + [(slot, items) for slot, items in day_meals(foods) if slot is not MealSlot.LUNCH]
    solved = solve_day_portions(meals, DAILY, nutrition_config)
    tuna = next(
        p
        for m in solved
        if m.slot is MealSlot.LUNCH
        for p in m.portions
        if foods["atún en agua"].id == p.food_id
    )
    assert tuna.grams % 100 == 0
    assert tuna.grams >= 100


def test_olives_never_exceed_snack_portion_cap(foods, nutrition_config) -> None:
    """Las aceitunas son un toque pequeño — el solver no las infla sin límite."""
    lunch = [foods["pechuga de pollo"], foods["arroz blanco cocido"], foods["aceitunas"]]
    dinner = [foods["tilapia"], foods["batata cocida"], foods["aceitunas"]]
    meals = [
        (MealSlot.LUNCH, lunch),
        (MealSlot.DINNER, dinner),
    ] + [
        (slot, items)
        for slot, items in day_meals(foods)
        if slot not in (MealSlot.LUNCH, MealSlot.DINNER)
    ]
    solved = solve_day_portions(meals, DAILY, nutrition_config)
    lookup = {f.id: f for f in foods.values()}
    for meal in solved:
        for p in meal.portions:
            if lookup[p.food_id].name_es == "aceitunas":
                assert p.grams <= 30, p.grams


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
            {"slot": slot, "food_ids": [str(x) for x in food_ids]} for slot, food_ids in ids.items()
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
    day["meals"][1]["food_ids"] = [  # 3 ítems: un snack no es una comida
        str(foods["yogur griego natural"].id),
        str(foods["banano"].id),
        str(foods["almendras"].id),
    ]
    day["meals"][2]["food_ids"] = [
        str(foods["pechuga de pollo"].id),
        str(foods["brócoli"].id),  # verdura porcionada
    ]
    selection = _selection([day] + [_full_day(foods, i) for i in range(1, 7)])
    reasons = {v.reason for v in validate_selection_structure(selection, lookup)}
    assert "falta fuente de proteína" in reasons
    assert "máximo 2 alimentos" in reasons
    assert any("ensalada libre" in r for r in reasons)
    assert any("falta" in r and "carbohidrato" in r for r in reasons)


def test_snack_can_be_just_a_fruit(foods) -> None:
    """Un snack no es una comida en pequeño: una fruta sola basta.

    Antes el snack exigía proteína Y carbohidrato, y por eso el motor acababa
    poniendo huevo duro a media mañana.
    """
    lookup = {str(f.id): f for f in foods.values()}
    days = []
    for i in range(7):
        day = _full_day(foods, i)
        day["meals"][1]["food_ids"] = [str(foods["banano"].id)]  # solo fruta
        day["meals"][3]["food_ids"] = [  # fruta + crema de frutos secos
            str(foods["manzana"].id),
            str(foods["almendras"].id),
        ]
        days.append(day)
    assert validate_selection_structure(_selection(days), lookup) == []


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
    violations = check_variety(
        _selection(days),
        lookup,
        max_protein_repeats=3,
        max_carb_repeats=4,
        available=slot_availability(list(foods.values())),
    )
    # Hay muchas proteínas de almuerzo disponibles → el límite se queda en el tope
    # de config (3) y el pollo, con 6 usos, lo supera.
    assert any(v.food_name == "pechuga de pollo" and v.times_used == 6 for v in violations)


def test_variety_counts_a_food_across_slots_not_per_slot(foods) -> None:
    """El mismo alimento en los dos snacks son 14 usos, no dos contadores de 7.

    Este era el agujero por el que pasaba el yogur: se contaba por
    `(alimento, slot)`, así que 7 usos en el snack de la mañana y 7 en el de la
    tarde eran dos cuentas independientes y ninguna pasaba del límite.
    """
    lookup = {str(f.id): f for f in foods.values()}
    yogur = foods["yogur griego natural"]
    days = []
    for i in range(7):
        d = _full_day(foods, i)
        for meal_index in (1, 3):  # snack_am y snack_pm
            d["meals"][meal_index]["food_ids"] = [str(yogur.id), str(foods["banano"].id)]
        days.append(d)
    violations = check_variety(
        _selection(days),
        lookup,
        max_protein_repeats=3,
        max_carb_repeats=4,
        available=slot_availability(list(foods.values())),
    )
    entry = next(v for v in violations if v.food_name == "yogur griego natural")
    assert entry.times_used == 14


def test_variety_relaxes_when_only_one_option(foods) -> None:
    """Lista pobre: una sola proteína viable no debe fallar la generación.

    Repetir es inevitable, y reventar la generación no ayuda a nadie: lo que se
    hace es AVISAR al entrenador (`meal_template.pool_health`).
    """
    lookup = {str(f.id): f for f in foods.values()}
    # pollo en los 7 almuerzos, y es la única proteína de almuerzo que existe
    selection = _selection([_full_day(foods, i) for i in range(7)])
    only_chicken = [
        f
        for f in foods.values()
        if f.name_es == "pechuga de pollo" or f.category is not FoodCategory.PROTEIN
    ]
    violations = check_variety(
        selection,
        lookup,
        max_protein_repeats=3,
        max_carb_repeats=4,
        available=slot_availability(only_chicken),
    )
    assert not any(v.food_name == "pechuga de pollo" for v in violations)


async def test_new_version_differs_from_previous(foods) -> None:
    """Anti-repetición: otra versión (seed distinto) da un menú diferente."""
    from nutriplan.adapters.llm.heuristic import HeuristicSelector

    allowed = list(foods.values())
    schema = build_selection_schema(allowed)
    v0 = await HeuristicSelector(allowed, 150.0, seed=0).select_plan(
        system="", prompt="", schema=schema, model="x"
    )
    v1 = await HeuristicSelector(allowed, 150.0, seed=1).select_plan(
        system="", prompt="", schema=schema, model="x"
    )
    menu0 = [tuple(m.food_ids) for d in v0.days for m in d.meals]
    menu1 = [tuple(m.food_ids) for d in v1.days for m in d.meals]
    assert menu0 != menu1  # el mes 2 no repite el menú del mes 1


async def test_heuristic_meals_are_appetizing(foods) -> None:
    """Destilado de menús reales: huevos como base del desayuno (mayoría de
    días), snacks ligeros (lácteo/fruta, nunca carne) y carnes en almuerzo/cena.
    """
    from nutriplan.adapters.llm.heuristic import HeuristicSelector
    from nutriplan.domain import meal_affinity

    allowed = list(foods.values())
    schema = build_selection_schema(allowed)
    sel = await HeuristicSelector(allowed, 124.0, seed=0).select_plan(
        system="", prompt="", schema=schema, model="x"
    )
    byid = {str(f.id): f for f in allowed}
    egg_breakfasts = 0
    for day in sel.days:
        meals = {m.slot: [byid[i] for i in m.food_ids] for m in day.meals}

        bfast = meals[MealSlot.BREAKFAST]
        prot = [f for f in bfast if f.category in (FoodCategory.PROTEIN, FoodCategory.DAIRY)]
        assert prot, "desayuno sin proteína"
        assert all(meal_affinity.breakfast_protein(f) for f in prot), (
            f"desayuno con proteína inapropiada: {[f.name_es for f in prot]}"
        )
        if any(meal_affinity.is_egg(f) for f in bfast):
            egg_breakfasts += 1

        for slot in (MealSlot.SNACK_AM, MealSlot.SNACK_PM):
            for f in meals[slot]:
                assert not meal_affinity.main_protein(f), (
                    f"{f.name_es} (carne/pescado) no va en un snack"
                )

        for slot in (MealSlot.LUNCH, MealSlot.DINNER):
            assert any(meal_affinity.main_protein(f) for f in meals[slot]), (
                f"{slot.value} sin proteína de plato principal"
            )

    assert egg_breakfasts >= 3, f"los huevos deben ser mayoritarios (fueron {egg_breakfasts}/7)"


async def test_every_item_belongs_in_the_slot_it_was_put_in(foods) -> None:
    """La afinidad vive en `FoodItem.meal_slots` y el selector la respeta.

    Antes solo la miraban las proteínas y los carbos, así que el pool de grasas
    metía dos cucharadas de aceite de oliva en el desayuno: el dato existía y
    nadie lo leía.
    """
    from nutriplan.adapters.llm.heuristic import HeuristicSelector
    from nutriplan.domain import meal_affinity

    allowed = list(foods.values())
    sel = await HeuristicSelector(allowed, 124.0, seed=0).select_plan(
        system="", prompt="", schema=build_selection_schema(allowed), model="x"
    )
    byid = {str(f.id): f for f in allowed}
    for day in sel.days:
        for meal in day.meals:
            for food in (byid[i] for i in meal.food_ids):
                assert meal_affinity.allows(food, meal.slot), (
                    f"{food.name_es} no encaja en {meal.slot.value} "
                    f"(sus comidas: {[s.value for s in food.meal_slots]})"
                )


async def test_every_day_carries_two_servings_of_fruit(foods) -> None:
    """La regla de digestión sale de la ESTRUCTURA, no de una validación aparte.

    Los dos snacks declaran `fruit_as_carb` y `carb_optional=False`, así que
    ninguna semana puede salir sin fruta en ambos. Es lo que hacen los planes
    reales, y el motor no puede desviarse de ello ni queriendo.
    """
    from nutriplan.adapters.llm.heuristic import HeuristicSelector

    allowed = list(foods.values())
    sel = await HeuristicSelector(allowed, 124.0, seed=0).select_plan(
        system="", prompt="", schema=build_selection_schema(allowed), model="x"
    )
    byid = {str(f.id): f for f in allowed}
    for day in sel.days:
        fruit = [
            byid[i].name_es
            for m in day.meals
            for i in m.food_ids
            if byid[i].category is FoodCategory.FRUIT
        ]
        assert len(fruit) >= 2, f"día {day.day_index}: solo {len(fruit)} frutas ({fruit})"


async def test_heuristic_snacks_are_light_and_varied_and_never_eggs(foods) -> None:
    """El snack es saciedad: fruta, fruta con grasa, o lácteo con fruta. Nunca huevo.

    Dos cosas se prueban a la vez porque nacen del mismo error: cuando el snack
    tenía que aportar su cuota de proteína como cualquier otra comida, el motor
    metía huevo duro a media mañana — y con un solo lácteo en la lista, el mismo
    yogur catorce veces.
    """
    from nutriplan.adapters.llm.heuristic import HeuristicSelector

    allowed = list(foods.values())
    sel = await HeuristicSelector(allowed, 124.0, seed=0).select_plan(
        system="", prompt="", schema=build_selection_schema(allowed), model="x"
    )
    byid = {str(f.id): f for f in allowed}
    snacks: list[list[str]] = []
    for day in sel.days:
        for slot in (MealSlot.SNACK_AM, MealSlot.SNACK_PM):
            ids = next(m.food_ids for m in day.meals if m.slot is slot)
            snacks.append([byid[fid].name_es for fid in ids])

    for snack in snacks:
        assert len(snack) <= 2, f"snack de {len(snack)} ítems: {snack}"
        assert not any("huevo" in byid_name for byid_name in snack), snack

    counts = Counter(name for snack in snacks for name in snack)
    assert max(counts.values()) <= 7, f"un alimento domina los snacks: {counts}"
    # Y las formas varían: alguna vez el snack es solo una fruta.
    assert any(len(snack) == 1 for snack in snacks), snacks


def test_fiber_informs_but_never_blocks_the_plan(foods, nutrition_config) -> None:
    """La fibra es un aviso, no una compuerta.

    Depende de lo que al cliente le guste comer: negarle el plan a quien no
    soporta las legumbres sería absurdo. Se calcula, se reporta, y el entrenador
    decide.
    """
    solved = solve_day_portions(day_meals(foods), DAILY, nutrition_config)
    assert sum(m.computed.fiber_g for m in solved) > 0  # se calcula de verdad

    hambriento = DAILY.model_copy(update={"fiber_g": 500.0})  # objetivo imposible
    assert fiber_shortfall(solved, hambriento) > 0  # lo reporta...
    assert validate_day(solved, hambriento, nutrition_config) == []  # ...y no bloquea


def test_variety_rotated_week_is_clean(foods) -> None:
    lookup = {str(f.id): f for f in foods.values()}
    days = []
    for i in range(7):
        d = _full_day(foods, i)
        proteins = ["pechuga de pollo", "tilapia", "carne de res magra"]
        carbs = ["arroz blanco cocido", "papa cocida", "quinoa cocida", "batata cocida"]
        d["meals"][2]["food_ids"] = [str(foods[proteins[i % 3]].id), str(foods[carbs[i % 4]].id)]
        d["meals"][4]["food_ids"] = [
            str(foods[proteins[(i + 1) % 3]].id),
            str(foods[carbs[(i + 1) % 4]].id),
        ]
        days.append(d)
    assert check_variety(_selection(days), lookup, max_protein_repeats=3, max_carb_repeats=4) == []


def test_the_day_with_a_free_meal_aims_lower_and_the_rest_of_it_does_not_move(
    nutrition_config,
) -> None:
    """El corazón de la comida libre.

    Quitar el slot y ya estaría MAL: `macro_shares` renormaliza sobre las comidas
    que quedan, así que las cuatro restantes se repartirían el día entero y el día
    cuadraría el objetivo completo — con porciones más grandes. Lo que se descuenta
    es el PESO de la comida libre, y entonces las demás conservan su objetivo
    ABSOLUTO de siempre.
    """
    daily = MacroTargets(kcal=2000, protein_g=150, carb_g=200, fat_g=60, fiber_g=28)
    reduced = daily_minus_free_meal(daily, nutrition_config, MealSlot.DINNER)

    dinner = nutrition_config.meal_distribution[MealSlot.DINNER]
    assert reduced.protein_g == round(daily.protein_g * (1 - dinner.protein_g), 1)
    assert reduced.kcal < daily.kcal  # el día suma por debajo: eso es una comida libre

    # Y la prueba de fuego: el almuerzo pide lo mismo que pediría un día normal.
    lunch = nutrition_config.meal_distribution[MealSlot.LUNCH]
    normal = daily.protein_g * lunch.protein_g
    with_free = reduced.protein_g * (lunch.protein_g / (1 - dinner.protein_g))
    assert round(with_free, 1) == round(normal, 1)

    # Sin comida libre no cambia nada.
    assert daily_minus_free_meal(daily, nutrition_config, None) == daily


def test_the_free_meal_is_the_only_slot_allowed_to_be_missing() -> None:
    """`drop_free_meal` la quita y el validador de estructura no la echa de menos."""
    foods = catalog_by_name()
    ids = {str(f.id): f for f in foods.values()}
    pollo, arroz, huevo, avena, banano, aguacate = (
        foods["pechuga de pollo"],
        foods["arroz blanco cocido"],
        foods["huevo entero"],
        foods["avena en hojuelas"],
        foods["banano"],
        foods["aguacate"],
    )
    day_meals = [
        MealSelection(slot=MealSlot.BREAKFAST, food_ids=[str(huevo.id), str(avena.id)]),
        MealSelection(slot=MealSlot.SNACK_AM, food_ids=[str(banano.id)]),
        MealSelection(slot=MealSlot.LUNCH, food_ids=[str(pollo.id), str(arroz.id)]),
        MealSelection(slot=MealSlot.SNACK_PM, food_ids=[str(banano.id)]),
        MealSelection(slot=MealSlot.DINNER, food_ids=[str(pollo.id), str(aguacate.id)]),
    ]
    selection = PlanSelection(
        days=[DaySelection(day_index=d, meals=list(day_meals)) for d in range(7)]
    )

    free_meal = (6, MealSlot.DINNER)
    dropped = drop_free_meal(selection, free_meal)

    # La celda ya no está — y solo esa.
    day6 = next(d for d in dropped.days if d.day_index == 6)
    assert MealSlot.DINNER not in {m.slot for m in day6.meals}
    assert len(day6.meals) == 4
    assert all(len(d.meals) == 5 for d in dropped.days if d.day_index != 6)

    # Y el validador no la reporta como "slot faltante".
    assert validate_selection_structure(dropped, ids, free_meal=free_meal) == []
    # Sin decirle cuál es la libre, sí protesta: la excepción es explícita.
    assert validate_selection_structure(dropped, ids) != []
