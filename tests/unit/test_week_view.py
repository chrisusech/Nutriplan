"""Cómo se ve la semana: lo que la persona lee en su pantalla.

Es la última traducción antes del HTML, así que aquí se decide si un plato se
llama "Tostada de huevos con aguacate" o "pan, huevo, aguacate".
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.models import (
    DayPlan,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
    PlanCycle,
    PlanStatus,
)
from nutriplan.ui.web import week_view


def _food(nombre: str, **kwargs) -> FoodItem:
    return FoodItem(
        id=uuid4(), source="curated", name_es=nombre,
        category="protein", kcal_100g=100, protein_100g=10, carb_100g=5, fat_100g=3,
        **kwargs,
    )


def _meal(slot: MealSlot, foods: list[FoodItem], **kwargs) -> MealEntry:
    return MealEntry(
        slot=slot,
        items=[MealItem(food_id=f.id, grams=100, position=i) for i, f in enumerate(foods)],
        computed=MacroTargets(kcal=100 * len(foods), protein_g=10, carb_g=5, fat_g=3),
        **kwargs,
    )


def _catalogo(foods: list[FoodItem]) -> dict[UUID, FoodItem]:
    return {f.id: f for f in foods}


# --- El nombre del plato ----------------------------------------------------


def test_un_plato_con_nombre_se_llama_por_su_nombre() -> None:
    huevo, pan = _food("huevo"), _food("pan integral")
    comida = _meal(MealSlot.BREAKFAST, [huevo, pan], dish_name="Tostada de huevos")

    vista = week_view.meal_view(comida, _catalogo([huevo, pan]))
    assert vista["title"] == "Tostada de huevos"


def test_sin_nombre_se_listan_sus_alimentos_antes_que_dejar_un_hueco() -> None:
    """Modo offline: el plato no tiene nombre, pero la persona tiene que poder
    leer qué le tocó comer."""
    huevo, pan = _food("huevo"), _food("pan integral")
    vista = week_view.meal_view(_meal(MealSlot.BREAKFAST, [huevo, pan]), _catalogo([huevo, pan]))
    assert vista["title"] == "Huevo con pan integral"


def test_si_no_queda_ni_un_alimento_se_usa_el_nombre_de_la_comida() -> None:
    vista = week_view.meal_view(_meal(MealSlot.LUNCH, []), {})
    assert vista["title"] == "Almuerzo"


# --- La comida libre --------------------------------------------------------


def test_la_comida_libre_se_anuncia_como_tal_y_no_lleva_cuentas() -> None:
    """Es el premio de la semana: contarle las calorías sería perder el punto."""
    comida = MealEntry(
        slot=MealSlot.DINNER, items=[], is_free_meal=True,
        computed=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
    )
    vista = week_view.meal_view(comida, {})
    assert vista["is_free"] is True
    assert vista["title"] == "Comida libre"
    assert vista["kcal"] == 0
    assert vista["ingredients"] == []


def test_la_ensalada_y_la_proteina_libres_se_ven_en_los_ingredientes(  # noqa: D103
) -> None:
    pollo = _food("pollo")
    comida = _meal(MealSlot.LUNCH, [pollo], free_salad=True, free_protein=True)
    vista = week_view.meal_view(comida, _catalogo([pollo]))
    assert "Ensalada libre" in vista["ingredients"]
    assert "Proteína libre" in vista["ingredients"]


# --- Los días ---------------------------------------------------------------


_TOTALES = MacroTargets(kcal=2000, protein_g=150, carb_g=200, fat_g=60)


def _plan() -> PlanCycle:
    return PlanCycle(
        id=uuid4(), tenant_id=uuid4(), client_id=uuid4(), targets_id=uuid4(),
        status=PlanStatus.DRAFT, config_version="1", prompt_version="1",
        model="m", input_hash="h", created_at=datetime.now(UTC),
        days=[
            DayPlan(day_index=i, meals=[], totals=_TOTALES)
            for i in range(7)
        ],
    )


def test_la_semana_tiene_siete_dias_y_solo_uno_seleccionado() -> None:
    chips = week_view.day_chips(_plan(), selected=2, today=0)
    assert len(chips) == 7
    assert [c["on"] for c in chips].count(True) == 1
    assert chips[2]["on"] is True


def test_hoy_se_marca_para_no_tener_que_buscarlo() -> None:
    chips = week_view.day_chips(_plan(), selected=0, today=4)
    assert chips[4]["is_today"] is True
    assert sum(c["is_today"] for c in chips) == 1


def test_los_dias_salen_en_orden_aunque_lleguen_desordenados() -> None:
    """Un plan cuyos días vengan barajados no puede pintar la semana al revés."""
    plan = _plan()
    revuelto = plan.model_copy(update={"days": list(reversed(plan.days))})
    chips = week_view.day_chips(revuelto, selected=0, today=0)
    assert [c["index"] for c in chips] == list(range(7))


# --- El día entero ----------------------------------------------------------


def test_el_dia_trae_sus_comidas_con_receta_y_calificacion() -> None:
    pollo = _food("pollo")
    comida = _meal(MealSlot.LUNCH, [pollo], dish_name="Pollo al horno", dish_key="k1")
    dia = DayPlan(
        day_index=0, meals=[comida],
        totals=MacroTargets(kcal=600, protein_g=45, carb_g=50, fat_g=20),
    )
    receta = DishRecipe(
        dish_key="k1", template_id="t1", name_es="Pollo al horno",
        ingredients=["pollo 100 g"], steps=["Hornear 20 minutos."],
        prep_minutes=20, difficulty="facil",
    )

    vista = week_view.day_view(dia, _catalogo([pollo]), {"k1": receta}, {MealSlot.LUNCH: 5})
    almuerzo = vista["meals"][0]
    assert almuerzo["title"] == "Pollo al horno"
    assert almuerzo["steps"] == ["Hornear 20 minutos."]
    assert almuerzo["rating"] == 5


def test_un_plato_sin_receta_todavia_igual_se_muestra() -> None:
    """La receta llega después que el plan y puede fallar: no puede dejar la
    pantalla en blanco."""
    pollo = _food("pollo")
    dia = DayPlan(
        day_index=0, meals=[_meal(MealSlot.LUNCH, [pollo])],
        totals=MacroTargets(kcal=600, protein_g=45, carb_g=50, fat_g=20),
    )
    vista = week_view.day_view(dia, _catalogo([pollo]), {}, {})
    assert vista["meals"][0]["steps"] == []
    assert vista["meals"][0]["ingredients"] != []
