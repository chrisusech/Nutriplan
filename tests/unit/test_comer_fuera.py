"""Comer fuera: un plato de restaurante recuadra el día y sale de la compra."""

from pathlib import Path

from tests.fixtures.plan_builder import build_fixed_plan, catalog_by_name

from nutriplan.adapters.meals.restaurant_store import load_restaurant_catalog
from nutriplan.application.eating_out import apply_eating_out
from nutriplan.application.shopping_list import shopping_list_for_plan
from nutriplan.domain.calculation import energy_kcal
from nutriplan.domain.models import MacroTargets, MealSlot
from nutriplan.domain.restaurant import is_eating_out

ROOT = Path(__file__).resolve().parents[2]
CATALOG = load_restaurant_catalog(ROOT / "data" / "restaurants" / "catalog.yaml")


def _nutrition_config():
    from nutriplan.adapters.config_yaml import YamlConfigProvider

    return YamlConfigProvider(ROOT / "config" / "nutrition.default.yaml").get_nutrition_config()


def test_una_porcion_de_pepperoni_recuadra_el_resto_del_martes() -> None:
    plan, _foods, _ = build_fixed_plan()
    martes = next(d for d in plan.days if d.day_index == 1)
    resto, dish = CATALOG.find("dominos", "pepperoni") or (None, None)
    assert resto is not None and dish is not None
    foods = {f.id: f for f in catalog_by_name().values()}
    daily = martes.totals
    nuevo, _aviso = apply_eating_out(
        martes,
        slot=MealSlot.DINNER,
        restaurant_id="dominos",
        restaurant_name=resto.name,
        dish=dish,
        foods_by_id=foods,
        daily=daily,
        config=_nutrition_config().for_slots([m.slot for m in martes.meals]),
    )
    cena = next(m for m in nuevo.meals if m.slot is MealSlot.DINNER)
    assert is_eating_out(cena)
    assert cena.items == []
    assert cena.computed.kcal == energy_kcal(dish.protein_g, dish.carb_g, dish.fat_g)
    # El resto del día sigue ahí, con alimentos de casa.
    casa = [m for m in nuevo.meals if m.slot is not MealSlot.DINNER]
    assert all(m.items for m in casa)
    # La cena de casa ya no está: sus alimentos no pueden inflar la compra.
    solo = plan.model_copy(update={"days": [nuevo]})
    lineas = [ln.name_es.lower() for g in shopping_list_for_plan(solo, foods) for ln in g.lines]
    assert "tilapia" not in lineas


def test_el_catalogo_no_repite_el_sombrero_vueltiao() -> None:
    crepes = CATALOG.restaurant("crepes")
    assert crepes is not None
    ids = [d.id for d in crepes.dishes]
    assert ids.count("sombrero-vueltiao") == 1


def test_no_queda_comida_libre_en_un_dia_recien_generado() -> None:
    """La generación ya no marca is_free_meal; este test clava el contrato."""
    from nutriplan.domain.models import MacroTargets, MealEntry

    comida = MealEntry(
        slot=MealSlot.DINNER,
        items=[],
        computed=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
        is_free_meal=False,
    )
    assert not comida.is_free_meal
    assert not is_eating_out(comida)


def test_un_plato_que_no_existe_no_se_inventa() -> None:
    import pytest

    from nutriplan.application.eating_out import apply_eating_out, resolve_dish
    from nutriplan.domain.errors import ValidationError

    with pytest.raises(ValidationError, match="catálogo"):
        resolve_dish(CATALOG, "dominos", "no-existe")

    plan, _foods, _ = build_fixed_plan()
    martes = next(d for d in plan.days if d.day_index == 1)
    resto, dish = CATALOG.find("dominos", "pepperoni") or (None, None)
    assert resto is not None and dish is not None
    sin_cena = martes.model_copy(
        update={"meals": [m for m in martes.meals if m.slot is not MealSlot.DINNER]}
    )
    with pytest.raises(ValidationError, match="no está"):
        apply_eating_out(
            sin_cena,
            slot=MealSlot.DINNER,
            restaurant_id="dominos",
            restaurant_name=resto.name,
            dish=dish,
            foods_by_id={f.id: f for f in catalog_by_name().values()},
            daily=martes.totals,
            config=_nutrition_config().for_slots([m.slot for m in martes.meals]),
        )


def test_si_la_pizza_se_come_el_presupuesto_se_avisa() -> None:
    from nutriplan.application.eating_out import apply_eating_out
    from nutriplan.domain.models import MacroTargets

    plan, _foods, _ = build_fixed_plan()
    martes = next(d for d in plan.days if d.day_index == 1)
    resto, dish = CATALOG.find("dominos", "pepperoni") or (None, None)
    assert resto is not None and dish is not None
    foods = {f.id: f for f in catalog_by_name().values()}
    daily = MacroTargets(kcal=900, protein_g=60, carb_g=80, fat_g=25)
    _nuevo, aviso = apply_eating_out(
        martes,
        slot=MealSlot.DINNER,
        restaurant_id="dominos",
        restaurant_name=resto.name,
        dish=dish,
        foods_by_id=foods,
        daily=daily,
        config=_nutrition_config().for_slots([m.slot for m in martes.meals]),
        servings=4,
    )
    assert aviso is not None
    assert "pasas" in aviso.lower()


def test_con_tres_comidas_marcadas_la_pizza_no_pisa_lo_ya_comido() -> None:
    """El recuadre no toca desayuno/snack/almuerzo si ya los comiste."""
    plan, _foods, _ = build_fixed_plan()
    martes = next(d for d in plan.days if d.day_index == 1)
    locked_slots = (MealSlot.BREAKFAST, MealSlot.SNACK_AM, MealSlot.LUNCH)
    antes = {m.slot: m.computed.model_copy() for m in martes.meals if m.slot in locked_slots}
    comido = martes.model_copy(
        update={
            "meals": [
                m.model_copy(update={"eaten": True}) if m.slot in locked_slots else m
                for m in martes.meals
            ]
        }
    )
    resto, dish = CATALOG.find("dominos", "pepperoni") or (None, None)
    assert resto is not None and dish is not None
    nuevo, _aviso = apply_eating_out(
        comido,
        slot=MealSlot.DINNER,
        restaurant_id="dominos",
        restaurant_name=resto.name,
        dish=dish,
        foods_by_id={f.id: f for f in catalog_by_name().values()},
        daily=martes.totals,
        config=_nutrition_config().for_slots([m.slot for m in martes.meals]),
    )
    for slot, macros in antes.items():
        actual = next(m for m in nuevo.meals if m.slot is slot)
        assert actual.eaten is True
        assert actual.computed == macros
    cena = next(m for m in nuevo.meals if m.slot is MealSlot.DINNER)
    assert is_eating_out(cena)


def _daily_grande() -> MacroTargets:
    return MacroTargets(kcal=2782, protein_g=128, carb_g=424, fat_g=64)


def test_una_pizza_liviana_sube_el_almuerzo_que_falta() -> None:
    """El restaurante gasta poco: lo que aún no se comió absorbe el resto."""
    plan, _foods, _ = build_fixed_plan()
    martes = next(d for d in plan.days if d.day_index == 1)
    antes = next(m for m in martes.meals if m.slot is MealSlot.LUNCH).computed.kcal
    resto, dish = CATALOG.find("dominos", "pepperoni") or (None, None)
    assert resto is not None and dish is not None
    nuevo, aviso = apply_eating_out(
        martes,
        slot=MealSlot.DINNER,
        restaurant_id="dominos",
        restaurant_name=resto.name,
        dish=dish,
        foods_by_id={f.id: f for f in catalog_by_name().values()},
        daily=_daily_grande(),
        config=_nutrition_config().for_slots([m.slot for m in martes.meals]),
    )
    almuerzo = next(m for m in nuevo.meals if m.slot is MealSlot.LUNCH)
    assert almuerzo.computed.kcal > antes
    assert aviso is None or "faltan" not in aviso


def test_si_ya_comi_todo_se_avisa_el_hueco() -> None:
    """Sin platos que recuadrar, no se finge que el día llegó al objetivo."""
    plan, _foods, _ = build_fixed_plan()
    martes = next(d for d in plan.days if d.day_index == 1)
    comido = martes.model_copy(
        update={
            "meals": [
                m.model_copy(update={"eaten": True}) if m.slot is not MealSlot.DINNER else m
                for m in martes.meals
            ]
        }
    )
    resto, dish = CATALOG.find("dominos", "pepperoni") or (None, None)
    assert resto is not None and dish is not None
    _nuevo, aviso = apply_eating_out(
        comido,
        slot=MealSlot.DINNER,
        restaurant_id="dominos",
        restaurant_name=resto.name,
        dish=dish,
        foods_by_id={f.id: f for f in catalog_by_name().values()},
        daily=_daily_grande(),
        config=_nutrition_config().for_slots([m.slot for m in martes.meals]),
    )
    assert aviso is not None
    assert "faltan" in aviso


def test_el_catalogo_rechaza_restaurantes_duplicados() -> None:
    import pytest

    from nutriplan.domain.restaurant import Restaurant, RestaurantCatalog, RestaurantDish

    dish = RestaurantDish(id="a", name="Taco", protein_g=10, carb_g=20, fat_g=8)
    with pytest.raises(ValueError, match="duplicado"):
        RestaurantCatalog(
            restaurants=[
                Restaurant(id="x", name="Uno", dishes=[dish]),
                Restaurant(id="x", name="Dos", dishes=[dish]),
            ]
        )
    with pytest.raises(ValueError, match="plato duplicado"):
        RestaurantCatalog(
            restaurants=[
                Restaurant(
                    id="x",
                    name="Uno",
                    dishes=[dish, dish.model_copy(update={"name": "Otro"})],
                ),
            ]
        )


def test_buscar_sin_texto_devuelve_platos() -> None:
    hits = CATALOG.search("", limit=5)
    assert len(hits) == 5
    assert CATALOG.restaurant("no-existe") is None
    assert CATALOG.find("dominos", "no-existe") is None


def test_un_yaml_roto_no_arranca_el_catalogo(tmp_path) -> None:
    import pytest

    from nutriplan.adapters.meals.restaurant_store import (
        RestaurantCatalogError,
        load_restaurant_catalog,
    )

    ruta = tmp_path / "malo.yaml"
    ruta.write_text("restaurants: [", encoding="utf-8")
    with pytest.raises(RestaurantCatalogError):
        load_restaurant_catalog(ruta)


def test_anadir_restaurante_y_plato_al_yaml(tmp_path) -> None:
    from nutriplan.adapters.meals.restaurant_store import (
        append_dish,
        append_restaurant,
        load_restaurant_catalog,
    )
    from nutriplan.domain.restaurant import Restaurant, RestaurantDish

    ruta = tmp_path / "catalog.yaml"
    ruta.write_text('version: "1.0.0"\nrestaurants: []\n', encoding="utf-8")
    marca = Restaurant(id="demo", name="Demo", dishes=[])
    assert append_restaurant(ruta, marca) is True
    assert append_restaurant(ruta, marca) is False

    plato = RestaurantDish(id="bowl", name="Bowl", protein_g=30, carb_g=40, fat_g=15)
    assert append_dish(ruta, "demo", plato) is True
    assert append_dish(ruta, "demo", plato) is False

    leido = load_restaurant_catalog(ruta)
    assert leido.restaurant("demo") is not None
    dish = leido.find("demo", "bowl")
    assert dish is not None
    assert dish[1].kcal == 30 * 4 + 40 * 4 + 15 * 9


def test_anadir_plato_a_una_marca_que_no_existe_no_inventa(tmp_path) -> None:
    import pytest

    from nutriplan.adapters.meals.restaurant_store import (
        RestaurantCatalogError,
        append_dish,
    )
    from nutriplan.domain.restaurant import RestaurantDish

    ruta = tmp_path / "catalog.yaml"
    ruta.write_text('version: "1.0.0"\nrestaurants: []\n', encoding="utf-8")
    with pytest.raises(RestaurantCatalogError, match="No está"):
        append_dish(
            ruta,
            "fantasma",
            RestaurantDish(id="x", name="X", protein_g=1, carb_g=1, fat_g=1),
        )
