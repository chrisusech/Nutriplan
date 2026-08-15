"""Catálogo de platos: expansión, invariantes y selección determinista."""

import asyncio
from pathlib import Path

import pytest
from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.adapters.llm.template_selector import (
    InsufficientDishes,
    TemplateSelector,
)
from nutriplan.adapters.meals.template_store import load_meal_catalog
from nutriplan.domain.macro_split import macro_shares
from nutriplan.domain.meal_template import (
    Component,
    MealCatalog,
    MealCatalogError,
    MealTemplate,
    expand,
    pool_health,
    validate_catalog,
)
from nutriplan.domain.models import FoodCategory, MacroTargets, MealSlot
from nutriplan.domain.portioning import solve_day_portions
from nutriplan.domain.selection_schema import build_selection_schema
from nutriplan.domain.validation import validate_day

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ROOT / "data" / "meals" / "food_classes.yaml"
TEMPLATES = ROOT / "data" / "meals" / "meal_templates.yaml"

DAILY = MacroTargets(kcal=1800, protein_g=104.0, carb_g=200.0, fat_g=52.0)

# La lista EXACTA del cliente "Testing", el del plan que salía con yogur los siete
# días. Es el caso de regresión del bug reportado.
TESTING_FOODS = [
    "arepa de maíz",
    "arroz integral cocido",
    "batata cocida",
    "garbanzos cocidos",
    "maíz dulce",
    "pan integral",
    "yogur griego natural",
    "aceitunas",
    "aguacate",
    "mantequilla de almendras",
    "arándanos",
    "banano",
    "fresa",
    "kiwi",
    "mango",
    "papaya",
    "atún en agua",
    "carne molida de res",
    "huevo entero",
    "lentejas cocidas",
    "pechuga de pollo",
    "salmón",
    "tilapia",
    "brócoli",
    "cebolla",
    "espinaca",
    "lechuga",
    "pimentón",
    "zanahoria",
]


@pytest.fixture(scope="module")
def catalog() -> MealCatalog:
    return load_meal_catalog(CLASSES, TEMPLATES)


@pytest.fixture(scope="module")
def foods():
    return catalog_by_name()


@pytest.fixture(scope="module")
def testing_allowed(foods):
    return [foods[name] for name in TESTING_FOODS]


def test_the_shipped_catalog_is_valid(catalog, foods) -> None:
    """Los YAML que se despliegan tienen que cargar y ser consistentes."""
    validate_catalog(catalog, list(foods.values()))
    assert catalog.templates
    assert catalog.version


def test_catalog_version_changes_the_plan_hash(catalog) -> None:
    """La versión combina los dos ficheros; entra en compute_input_hash.

    Sin esto, editar meal_templates.yaml no cambiaría el hash, el plan viejo se
    serviría de la caché y los platos nuevos no aparecerían jamás.
    """
    assert "+" in catalog.version


def test_a_component_with_no_candidates_kills_the_dish(catalog, foods) -> None:
    """Si el cliente no tiene con qué cubrir un componente obligatorio, ese plato
    no se emite — no se emite a medias."""
    # Sin lácteos, "Lácteo con fruta" no puede existir.
    sin_lacteos = [f for f in foods.values() if f.category is not FoodCategory.DAIRY]
    pools = expand(catalog, sin_lacteos)
    for slot in (MealSlot.SNACK_AM, MealSlot.SNACK_PM):
        assert all(d.template_id != "lacteo_fruta" for d in pools[slot])


def test_an_optional_component_is_dropped_not_the_dish(catalog, foods) -> None:
    """El componente opcional que no se puede cubrir se omite; el plato sobrevive."""
    # `proteina_carbo_ensalada` lleva una grasa de cocina OPCIONAL.
    sin_grasas = [f for f in foods.values() if f.category is not FoodCategory.FAT]
    pools = expand(catalog, sin_grasas)
    dishes = [d for d in pools[MealSlot.LUNCH] if d.template_id == "proteina_carbo_ensalada"]
    assert dishes
    assert all(len(d.foods) == 2 for d in dishes)  # proteína + carbo, sin grasa


def test_a_component_whose_class_breaks_its_role_fails_at_load(catalog, foods) -> None:
    """Un plato mal declarado revienta al CARGAR, no en mitad de una generación."""
    roto = MealTemplate(
        id="roto",
        name="Fruta de proteína",
        slots=(MealSlot.SNACK_AM,),
        # La clase `fruta` resuelve a frutas, pero el rol dice que es la proteína.
        components=(Component(role=FoodCategory.PROTEIN, selector="@fruta"),),
    )
    bad = MealCatalog(
        version=catalog.version,
        classes=catalog.classes,
        templates=(roto,),
    )
    with pytest.raises(MealCatalogError, match="incompatible con el rol"):
        validate_catalog(bad, list(foods.values()))


def test_pool_health_warns_when_a_slot_has_too_few_dishes(catalog, foods) -> None:
    solo_uno = [
        foods["yogur griego natural"],
        foods["banano"],
        foods["huevo entero"],
        foods["pechuga de pollo"],
        foods["aguacate"],
        foods["pan integral"],
    ]
    pools = expand(catalog, solo_uno)
    warnings = pool_health(pools, minimum=5)
    assert warnings
    assert all(w.dish_count < 5 for w in warnings)


def test_selection_is_deterministic_and_the_seed_changes_it(catalog, foods) -> None:
    allowed = list(foods.values())
    sel = TemplateSelector(allowed, catalog, DAILY, seed=0)
    first = [{s: d.key for s, d in day.items()} for day in sel.select_week(seed=0)]
    again = [{s: d.key for s, d in day.items()} for day in sel.select_week(seed=0)]
    other = [{s: d.key for s, d in day.items()} for day in sel.select_week(seed=1)]
    assert first == again  # misma entrada → misma salida, bit a bit
    assert first != other  # el seed (mes 1 vs mes 2) da otro menú


def test_a_food_never_repeats_twice_in_the_same_day(catalog, foods) -> None:
    sel = TemplateSelector(list(foods.values()), catalog, DAILY, seed=0)
    for day in sel.select_week(seed=0):
        ids = [fid for dish in day.values() for fid in dish.food_ids]
        assert len(ids) == len(set(ids))


def test_testing_no_longer_eats_the_same_yogurt_seven_days(catalog, testing_allowed) -> None:
    """La regresión del bug reportado.

    Con la lista real de "Testing" —un solo lácteo—, el motor viejo servía el
    mismo yogur en el snack de la mañana los SIETE días: su pool de snacks tenía
    tamaño 2 y la rotación avanzaba de dos en dos, así que el índice
    `(2·día + offset) % 2` era constante toda la semana.
    """
    sel = TemplateSelector(testing_allowed, catalog, DAILY, seed=0)
    week = sel.select_week(seed=0)
    for slot in MealSlot:
        counts: dict[str, int] = {}
        for day in week:
            for food in day[slot].foods:
                if food.category in (FoodCategory.PROTEIN, FoodCategory.DAIRY):
                    counts[food.name_es] = counts.get(food.name_es, 0) + 1
        if counts:
            worst = max(counts.values())
            assert worst <= 5, f"{slot.value}: {counts}"


def test_no_snack_is_ever_a_hard_boiled_egg(catalog, foods) -> None:
    """El huevo duro no es un snack, y ningún plato de snack lo lleva.

    Se comprueba sobre el catálogo ENTERO, no sobre una lista concreta: la puerta
    está cerrada en el dato (el huevo ya no declara los slots de snack), así que
    ningún cliente puede acabar con uno a media mañana.
    """
    pools = expand(catalog, list(foods.values()))
    for slot in (MealSlot.SNACK_AM, MealSlot.SNACK_PM):
        assert pools[slot], f"{slot.value} se quedó sin platos"
        for dish in pools[slot]:
            assert not any("huevo" in f.tags for f in dish.foods), dish.name
            assert len(dish.foods) <= 2, f"{dish.name}: un snack no es una comida"


def test_testing_gets_a_real_breakfast_not_yogurt_and_bread(
    catalog, testing_allowed, nutrition_config
) -> None:
    """Ningún desayuno se queda sin una proteína de verdad, y el día cuadra."""
    sel = TemplateSelector(testing_allowed, catalog, DAILY, seed=0, config=nutrition_config)
    for day in sel.select_week(seed=0):
        meals = [(slot, list(dish.foods)) for slot, dish in day.items()]
        solved = solve_day_portions(meals, DAILY, nutrition_config)
        # Con el reparto REAL: un snack de solo fruta no debe proteína, y el
        # almuerzo lleva la que el snack no lleva.
        deviations = validate_day(
            solved, DAILY, nutrition_config, shares=macro_shares(meals, nutrition_config)
        )
        assert deviations == []


def test_a_client_with_nothing_to_cook_raises_instead_of_guessing(catalog, foods) -> None:
    """Sin platos posibles en un slot, el motor lo dice — no improvisa."""
    solo_verduras = [f for f in foods.values() if f.category is FoodCategory.VEGETABLE]
    with pytest.raises(InsufficientDishes):
        TemplateSelector(solo_verduras, catalog, DAILY, seed=0)


def test_the_selection_fits_the_schema_the_engine_expects(catalog, foods) -> None:
    """El plato expande a food_ids: PlanSelection no se entera de que existe."""
    allowed = list(foods.values())
    sel = TemplateSelector(allowed, catalog, DAILY, seed=0)
    schema = build_selection_schema(allowed)
    plan = asyncio.run(sel.select_plan(system="", prompt="", schema=schema, model=""))
    assert len(plan.days) == 7
    for day in plan.days:
        assert len(day.meals) == 5
        for meal in day.meals:
            assert 1 <= len(meal.food_ids) <= 4  # el tope del schema


def test_lunch_and_dinner_are_rice_and_potato_not_bread_and_arepa(catalog, testing_allowed) -> None:
    """El bug reportado: "en muchos almuerzos o cenas colocas pan o arepa".

    La lista de Testing tiene arepa y pan integral (que son de desayuno) junto a
    arroz integral, batata y maíz. El motor sabía que los cinco "podían" ir a un
    almuerzo y los trataba como equivalentes; ahora el peso dice de quién es cada
    comida y las principales se comen lo que se come a mediodía.
    """
    selector = TemplateSelector(testing_allowed, catalog, DAILY, seed=0)
    week = selector.select_week(seed=0)

    breads = {"arepa de maíz", "pan integral"}
    served = [
        (day, slot, food.name_es)
        for day, meals in enumerate(week)
        for slot in (MealSlot.LUNCH, MealSlot.DINNER)
        for food in meals[slot].foods
        if food.category is FoodCategory.CARB
    ]
    assert served, "los almuerzos y cenas llevan carbohidrato"
    bread_meals = [(d, s.value, n) for d, s, n in served if n in breads]
    assert not bread_meals, f"pan o arepa en comidas principales: {bread_meals}"

    # Y siguen siendo el desayuno: no se les ha echado del catálogo, se les ha puesto
    # en su sitio.
    breakfasts = {
        food.name_es
        for meals in week
        for food in meals[MealSlot.BREAKFAST].foods
        if food.category is FoodCategory.CARB
    }
    assert breakfasts & breads


def test_un_desayuno_alto_en_carbo_puede_ser_huevos_con_arepa(
    catalog, foods, nutrition_config
) -> None:
    """~130 g de carbo en el desayuno no cabe en arepa sola, pero sí con fruta.

    Sin eso el pool se queda en bowls de avena y la semana parece siempre lo
    mismo, aunque la receta IA le ponga otro nombre.
    """
    diario = MacroTargets(kcal=2900, protein_g=140.0, carb_g=430.0, fat_g=80.0)
    selector = TemplateSelector(
        list(foods.values()), catalog, diario, seed=0, config=nutrition_config
    )
    pool = selector.pools[MealSlot.BREAKFAST]
    huevos_arepa_fruta = [
        d
        for d in pool
        if d.template_id == "huevos_carbo_grasa"
        and any("arepa" in f.name_es.lower() for f in d.foods)
        and any(f.category is FoodCategory.FRUIT for f in d.foods)
    ]
    assert huevos_arepa_fruta, "huevos+arepa+fruta debía entrar al pool"

    desayunos = [meals[MealSlot.BREAKFAST] for meals in selector.select_week(seed=0)]
    plantillas = {d.template_id for d in desayunos}
    avena_fija = {"bowl_lacteo_avena_fruta", "batido_avena_fruta"}
    assert plantillas - avena_fija, f"los 7 desayunos fueron solo avena: {plantillas}"


def test_the_only_carb_you_have_is_the_one_you_eat(catalog, foods) -> None:
    """El peso ORDENA, no prohíbe.

    Si a un cliente solo le gusta la arepa, su almuerzo lleva arepa. Lo contrario
    —tratar el peso como un filtro— sería dejarlo sin plan por comer lo que come.
    """
    only_arepa = [
        foods[name]
        for name in (
            "arepa de maíz",
            "pechuga de pollo",
            "huevo entero",
            "yogur griego natural",
            "aguacate",
            "aceite de oliva",
            "banano",
            "fresa",
            "avena en hojuelas",
        )
    ]
    selector = TemplateSelector(only_arepa, catalog, DAILY, seed=0)  # no lanza
    week = selector.select_week(seed=0)

    lunch_carbs = {
        food.name_es
        for meals in week
        for food in meals[MealSlot.LUNCH].foods
        if food.category is FoodCategory.CARB
    }
    assert lunch_carbs == {"arepa de maíz"}


def test_an_optional_component_can_actually_be_left_out(catalog, foods) -> None:
    """ "Opcional" significaba "solo si no hay nada que lo cubra" — o sea, nunca.

    En cuanto existía UN candidato el componente pasaba a ser obligatorio, y el
    plato no tenía la versión que la plantilla promete ("proteína + carbohidrato +
    grasa OPCIONAL"). A un cliente cuya única grasa de cena es el aguacate se lo
    servía en el almuerzo Y en la cena, y el día se pasaba de grasa sin salida.
    """
    allowed = [
        foods[name] for name in ("pechuga de pollo", "arroz blanco cocido", "aceite de oliva")
    ]
    pool = expand(catalog, allowed)[MealSlot.LUNCH]
    assert pool, "el almuerzo tiene platos"

    with_fat = [d for d in pool if any(f.category is FoodCategory.FAT for f in d.foods)]
    without_fat = [d for d in pool if not any(f.category is FoodCategory.FAT for f in d.foods)]
    assert with_fat, "la grasa existe y el plato completo se puede servir"
    assert without_fat, "y también la versión sin ella, que es lo que 'opcional' dice"
    assert all(d.dropped == 0 for d in with_fat)
    assert all(d.dropped == 1 for d in without_fat)


def test_todas_las_plantillas_traen_una_receta_que_se_puede_leer() -> None:
    """El catálogo REAL, no uno de mentira.

    Un paso con dos puntos ("Sirve al momento: la avena espesa...") YAML lo
    parsea como diccionario, no como texto, y la receta entera se cae al
    validar. Pasó de verdad; los tests con datos sintéticos no lo veían.
    """
    from nutriplan.domain.meal_template import static_recipes

    catalog = load_meal_catalog(CLASSES, TEMPLATES)
    recetas = static_recipes(catalog)

    assert len(recetas) == len(catalog.templates), "toda plantilla necesita su receta"
    for template_id, steps in recetas.items():
        assert steps, f"{template_id} tiene la clave `recipe` vacía"
        for step in steps:
            assert isinstance(step, str), f"{template_id}: un paso no es texto ({step!r})"
            assert step.strip(), f"{template_id}: paso vacío"


def test_las_recetas_del_catalogo_no_llevan_cifras() -> None:
    """Las cantidades las pone el plan. Una receta que diga "200 g" miente."""
    import re

    from nutriplan.domain.meal_template import static_recipes

    catalog = load_meal_catalog(CLASSES, TEMPLATES)
    for template_id, steps in static_recipes(catalog).items():
        for step in steps:
            assert not re.search(r"\d+\s*(g|gr|gramos|kcal|ml)\b", step, re.I), (
                f"{template_id} mete cantidades en la receta: {step!r}"
            )
