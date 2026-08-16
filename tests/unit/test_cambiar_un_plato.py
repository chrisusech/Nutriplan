"""Cambiar un plato: el motor elige otro y el solver recuadra el día."""

from pathlib import Path

from tests.fixtures.plan_builder import build_fixed_plan, catalog_by_name

from nutriplan.adapters.llm.template_selector import TemplateSelector
from nutriplan.adapters.meals.template_store import load_meal_catalog
from nutriplan.application.swap_meal import swap_slot
from nutriplan.domain.models import MacroTargets, MealSlot

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ROOT / "data" / "meals" / "food_classes.yaml"
TEMPLATES = ROOT / "data" / "meals" / "meal_templates.yaml"
DAILY = MacroTargets(kcal=2200, protein_g=150.0, carb_g=230.0, fat_g=70.0)


def test_cambiar_la_cena_deja_otro_plato_en_el_mismo_slot() -> None:
    plan, _foods, _ = build_fixed_plan()
    lunes = next(d for d in plan.days if d.day_index == 0)
    antes = next(m for m in lunes.meals if m.slot is MealSlot.DINNER)
    foods = list(catalog_by_name().values())
    foods_by_id = {f.id: f for f in foods}
    selector = TemplateSelector(foods, load_meal_catalog(CLASSES, TEMPLATES), DAILY, seed=3)
    from nutriplan.adapters.config_yaml import YamlConfigProvider

    config = YamlConfigProvider(ROOT / "config" / "nutrition.default.yaml").get_nutrition_config()
    slots = [m.slot for m in lunes.meals]
    ranked = selector.rank_slot(
        MealSlot.DINNER,
        exclude_keys=frozenset(filter(None, [antes.dish_key])),
        note="pollo",
    )
    assert ranked
    nuevo = swap_slot(
        lunes,
        slot=MealSlot.DINNER,
        pick=ranked[0],
        foods_by_id=foods_by_id,
        daily=DAILY,
        config=config.for_slots(slots),
    )
    despues = next(m for m in nuevo.meals if m.slot is MealSlot.DINNER)
    assert despues.dish_key != antes.dish_key
    assert despues.items
    assert len(nuevo.meals) == len(lunes.meals)


def _selector() -> TemplateSelector:
    foods = list(catalog_by_name().values())
    return TemplateSelector(foods, load_meal_catalog(CLASSES, TEMPLATES), DAILY, seed=3)


def test_el_nombre_del_plato_no_es_lo_que_escribio() -> None:
    """«Quisiera comer pollo sudado» no puede ser el título de la tarjeta."""
    plan, _foods, _ = build_fixed_plan()
    lunes = next(d for d in plan.days if d.day_index == 0)
    foods = list(catalog_by_name().values())
    foods_by_id = {f.id: f for f in foods}
    pick = _selector().rank_slot(MealSlot.LUNCH, note="pollo")[0]
    wish = "Quisiera comer pollo sudado con papa"
    nuevo = swap_slot(
        lunes,
        slot=MealSlot.LUNCH,
        pick=pick,
        foods_by_id=foods_by_id,
        daily=DAILY,
        config=_config().for_slots([m.slot for m in lunes.meals]),
        wish=wish,
    )
    almuerzo = next(m for m in nuevo.meals if m.slot is MealSlot.LUNCH)
    assert almuerzo.dish_name != wish
    assert almuerzo.dish_name == pick.name[:80]


def test_si_pide_pollo_con_papa_no_sale_pescado() -> None:
    """La nota manda: pollo y papa, no el primer plato del ranking."""
    ranked = _selector().rank_slot(MealSlot.LUNCH, note="pollo sudado con papa")
    assert ranked, "el catálogo tiene platos de pollo con papa en el almuerzo"
    hay = " ".join([ranked[0].name.lower(), *[f.name_es.lower() for f in ranked[0].foods]])
    assert "pollo" in hay
    assert "papa" in hay
    assert "tilapia" not in hay
    assert "bacalao" not in hay


def test_si_pide_algo_que_no_hay_no_se_inventa_un_pescado() -> None:
    ranked = _selector().rank_slot(MealSlot.LUNCH, note="unicornio azul")
    assert ranked == []


def test_una_frase_de_pechuga_aguacate_y_papa_encuentra_alimentos() -> None:
    """«Quisiera comer…» no es alimento: se lee pechuga, aguacate y papa."""
    from nutriplan.domain.swap_note import dish_from_foods, foods_from_note

    foods = list(catalog_by_name().values())
    note = "Quisiera comer pechuga de pollo con aguacate y papa"
    found = foods_from_note(note, foods)
    hay = " ".join(f.name_es.lower() for f in found)
    assert "pollo" in hay or "pechuga" in hay
    assert "aguacate" in hay
    assert "papa" in hay
    dish = dish_from_foods(MealSlot.BREAKFAST, found, name=note[:80])
    assert dish is not None
    assert len(dish.foods) >= 3
    plan, _foods, _ = build_fixed_plan()
    lunes = next(d for d in plan.days if d.day_index == 0)
    foods_by_id = {f.id: f for f in foods}
    nuevo = swap_slot(
        lunes,
        slot=MealSlot.BREAKFAST,
        pick=dish,
        foods_by_id=foods_by_id,
        daily=DAILY,
        config=_config().for_slots([m.slot for m in lunes.meals]),
        wish=note[:80],
    )
    desayuno = next(m for m in nuevo.meals if m.slot is MealSlot.BREAKFAST)
    assert (
        "pechuga" in (desayuno.dish_name or "").lower()
        or "pollo" in (desayuno.dish_name or "").lower()
    )
    assert desayuno.items


def test_si_el_catalogo_no_tiene_el_plato_la_ia_nombra_alimentos() -> None:
    """La IA solo elige alias; el código arma el plato."""
    import asyncio

    from nutriplan.application.swap_wish import interpret_swap_wish
    from nutriplan.domain.critique import food_aliases

    foods = list(catalog_by_name().values())
    usable = [f for f in foods if MealSlot.LUNCH in f.meal_slots] or foods
    aliases = food_aliases(usable)
    by_id = {fid: alias for alias, fid in aliases.items()}
    picked = []
    for name in ("pechuga de pollo", "aguacate", "papa cocida"):
        food = next(f for f in usable if f.name_es.lower() == name)
        picked.append(by_id[food.id])

    class _LLM:
        async def extract(self, *, system: str, text: str, schema: type, model: str):  # type: ignore[no-untyped-def]
            return schema.model_validate(
                {
                    "food_ids": picked,
                    "dish_name": "Pechuga con aguacate y papa",
                    "free_salad": False,
                }
            )

        async def select_plan(self, **kwargs: object):  # type: ignore[no-untyped-def]
            raise NotImplementedError

        def pop_usage(self) -> dict[str, int]:
            return {}

    dish = asyncio.run(
        interpret_swap_wish(
            note="algo rico de pollo con aguacate",
            slot=MealSlot.LUNCH,
            allowed=foods,
            llm=_LLM(),
            prompts_dir=ROOT / "prompts",
            model="test",
        )
    )
    assert dish is not None
    hay = " ".join(f.name_es.lower() for f in dish.foods)
    assert "pollo" in hay
    assert "aguacate" in hay


def test_no_se_cambia_un_plato_de_calle() -> None:
    import pytest

    from nutriplan.adapters.meals.restaurant_store import load_restaurant_catalog
    from nutriplan.application.eating_out import make_eating_out_entry
    from nutriplan.domain.errors import ValidationError

    plan, _foods, _ = build_fixed_plan()
    lunes = next(d for d in plan.days if d.day_index == 0)
    catalog = load_restaurant_catalog(ROOT / "data" / "restaurants" / "catalog.yaml")
    resto, dish = catalog.find("dominos", "pepperoni") or (None, None)
    assert resto is not None and dish is not None
    out = make_eating_out_entry(
        slot=MealSlot.DINNER,
        restaurant_id="dominos",
        restaurant_name=resto.name,
        dish=dish,
    )
    day = lunes.model_copy(
        update={"meals": [m if m.slot is not MealSlot.DINNER else out for m in lunes.meals]}
    )
    foods = {f.id: f for f in catalog_by_name().values()}
    selector = TemplateSelector(list(foods.values()), load_meal_catalog(CLASSES, TEMPLATES), DAILY)
    with pytest.raises(ValidationError, match="calle"):
        swap_slot(
            day,
            slot=MealSlot.DINNER,
            pick=selector.rank_slot(MealSlot.DINNER)[0],
            foods_by_id=foods,
            daily=DAILY,
            config=_config(),
        )


def test_cambiar_la_cena_no_pisa_un_desayuno_ya_comido() -> None:
    plan, _foods, _ = build_fixed_plan()
    lunes = next(d for d in plan.days if d.day_index == 0)
    desayuno = next(m for m in lunes.meals if m.slot is MealSlot.BREAKFAST)
    macros = desayuno.computed.model_copy()
    comido = lunes.model_copy(
        update={
            "meals": [
                m.model_copy(update={"eaten": True}) if m.slot is MealSlot.BREAKFAST else m
                for m in lunes.meals
            ]
        }
    )
    foods = list(catalog_by_name().values())
    foods_by_id = {f.id: f for f in foods}
    selector = TemplateSelector(foods, load_meal_catalog(CLASSES, TEMPLATES), DAILY, seed=3)
    ranked = selector.rank_slot(
        MealSlot.DINNER,
        exclude_keys=frozenset(
            filter(None, [m.dish_key for m in comido.meals if m.slot is MealSlot.DINNER])
        ),
        note="pollo",
    )
    assert ranked
    nuevo = swap_slot(
        comido,
        slot=MealSlot.DINNER,
        pick=ranked[0],
        foods_by_id=foods_by_id,
        daily=DAILY,
        config=_config().for_slots([m.slot for m in comido.meals]),
    )
    sigue = next(m for m in nuevo.meals if m.slot is MealSlot.BREAKFAST)
    assert sigue.eaten is True
    assert sigue.computed == macros


def test_un_slot_que_no_esta_en_el_dia_no_se_cambia() -> None:
    import pytest

    from nutriplan.domain.errors import ValidationError

    plan, _foods, _ = build_fixed_plan()
    lunes = next(d for d in plan.days if d.day_index == 0)
    foods = {f.id: f for f in catalog_by_name().values()}
    selector = TemplateSelector(list(foods.values()), load_meal_catalog(CLASSES, TEMPLATES), DAILY)
    ranked = selector.rank_slot(MealSlot.DINNER)
    sin_cena = lunes.model_copy(
        update={"meals": [m for m in lunes.meals if m.slot is not MealSlot.DINNER]}
    )
    with pytest.raises(ValidationError, match="no está"):
        swap_slot(
            sin_cena,
            slot=MealSlot.DINNER,
            pick=ranked[0],
            foods_by_id=foods,
            daily=DAILY,
            config=_config(),
        )


def _config():
    from nutriplan.adapters.config_yaml import YamlConfigProvider

    return YamlConfigProvider(ROOT / "config" / "nutrition.default.yaml").get_nutrition_config()


def test_lo_mismo_sin_pan_entiende_la_negacion_y_el_typo() -> None:
    from nutriplan.domain.swap_note import parse_swap_note

    foods = list(catalog_by_name().values())
    intent = parse_swap_note("Quiero lo mismo sin plan blanco", foods)
    assert intent.keep_same is True
    assert "pan" in intent.exclude
    assert intent.missing_dish is False


def test_papas_a_la_francesa_no_estan_en_el_catalogo() -> None:
    from nutriplan.domain.swap_note import parse_swap_note

    foods = list(catalog_by_name().values())
    intent = parse_swap_note("Carne con papas a la francesa", foods)
    assert intent.missing_dish is True
