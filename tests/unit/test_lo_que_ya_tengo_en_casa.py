"""Decir "esto ya lo tengo" tiene que acortar la compra, no empobrecer la semana.

La tentación al implementarlo era subir el peso hasta que el menú se llenara de
lo que hay en la nevera. Eso convierte una comodidad en un castigo: quien nos
cuenta lo que tiene acabaría comiendo arroz siete noches. Estos tests fijan el
equilibrio — se aprovecha, pero la variedad y el sentido común de cocina siguen
por encima— y de paso vigilan el eslabón fácil de olvidar: que el alimento llegue
al pool, no solo a la función de coste.
"""

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.adapters.llm.template_selector import TemplateSelector
from nutriplan.adapters.meals.template_store import load_meal_catalog
from nutriplan.application.shopping_list import shopping_list_for_plan
from nutriplan.domain.meal_template import Component, candidates_for
from nutriplan.domain.models import (
    DayPlan,
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealFoodPortion,
    MealSlot,
    PlanCycle,
)

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ROOT / "data" / "meals" / "food_classes.yaml"
TEMPLATES = ROOT / "data" / "meals" / "meal_templates.yaml"
DAILY = MacroTargets(kcal=2200, protein_g=150.0, carb_g=230.0, fat_g=70.0)
VARIEDAD = {FoodCategory.PROTEIN, FoodCategory.DAIRY, FoodCategory.CARB, FoodCategory.FRUIT}


@pytest.fixture(scope="module")
def platos():
    return load_meal_catalog(CLASSES, TEMPLATES)


@pytest.fixture(scope="module")
def alimentos():
    return catalog_by_name()


def _semana(platos, alimentos, en_casa: list[str]):
    ids = frozenset(alimentos[n].id for n in en_casa)
    selector = TemplateSelector(list(alimentos.values()), platos, DAILY, seed=7, on_hand_ids=ids)
    return selector.select_week(seed=7)


def _nombres(semana) -> set[str]:
    return {f.name_es for dia in semana for plato in dia.values() for f in plato.foods}


def _cuenta(semana, nombre: str) -> int:
    return sum(
        1 for dia in semana for plato in dia.values() for f in plato.foods if f.name_es == nombre
    )


def test_lo_que_dice_tener_en_casa_acaba_en_su_semana(platos, alimentos) -> None:
    """Al marcar quinoa, tiene que aparecer; no basta con que el coste la mire."""
    sin = _cuenta(_semana(platos, alimentos, []), "quinoa cocida")
    con = _cuenta(_semana(platos, alimentos, ["quinoa cocida"]), "quinoa cocida")
    assert con > 0
    assert con >= sin


def test_tener_pan_en_casa_no_convierte_la_cena_en_pan(platos, alimentos) -> None:
    """El pan declara que no es de cena. Eso no es una preferencia, es cocina:
    tiene que ganarle a la comodidad de gastar lo que hay en la despensa."""
    semana = _semana(platos, alimentos, ["pan integral"])
    cenas = {f.name_es for dia in semana for f in dia[MealSlot.DINNER].foods}
    assert "pan integral" not in cenas


def test_una_nevera_llena_no_deja_la_semana_comiendo_siempre_lo_mismo(platos, alimentos) -> None:
    en_casa = [
        "quinoa cocida",
        "arroz blanco cocido",
        "huevo entero",
        "banano",
        "papa cocida",
        "pechuga de pollo",
        "yogur griego natural",
        "avena en hojuelas",
        "manzana",
        "atún en agua",
    ]
    veces = Counter(
        f.name_es
        for dia in _semana(platos, alimentos, en_casa)
        for plato in dia.values()
        for f in plato.foods
        if f.category in VARIEDAD
    )
    assert veces.most_common(1)[0][1] <= 4, f"un alimento se repite de más: {veces.most_common(3)}"
    assert len(veces) >= 30, f"la semana se quedó en {len(veces)} alimentos distintos"


def _carbo(nombre: str, peso: int) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        source="curated",
        name_es=nombre,
        category=FoodCategory.CARB,
        kcal_100g=120,
        protein_100g=3,
        carb_100g=25,
        fat_100g=1,
        meal_slots=[MealSlot.LUNCH],
        slot_weights={MealSlot.LUNCH: peso},
    )


def test_el_alimento_de_casa_entra_al_pool_aunque_no_sea_de_los_mejores(platos) -> None:
    """El corte por afinidad se hace ANTES de la función de coste.

    `expand` se queda con los seis candidatos de más afinidad por componente. Un
    alimento de casa con afinidad normal quedaba fuera de ese corte y jamás
    llegaba al coste: preferirlo allí no servía de nada porque no estaba en
    ningún plato del pool.
    """
    buenos = [_carbo(f"carbo bueno {i}", 3) for i in range(8)]
    mio = _carbo("mi arroz", 2)
    componente = Component(role=FoodCategory.CARB, selector="@carbo_principal")

    sin_marca = candidates_for(platos, componente, [*buenos, mio], MealSlot.LUNCH)
    assert mio not in sin_marca[:6]

    con_marca = candidates_for(
        platos, componente, [*buenos, mio], MealSlot.LUNCH, on_hand=frozenset({mio.id})
    )
    assert con_marca[0] is mio


def _plan_de_una_comida(alimentos: dict[str, FoodItem]) -> PlanCycle:
    pollo, arroz = alimentos["pechuga de pollo"], alimentos["arroz blanco cocido"]
    macros = MacroTargets(kcal=500, protein_g=40, carb_g=50, fat_g=8)
    comida = MealEntry(
        slot=MealSlot.LUNCH,
        portions=[
            MealFoodPortion(food_id=pollo.id, grams=150),
            MealFoodPortion(food_id=arroz.id, grams=200),
        ],
        computed=macros,
    )
    return PlanCycle(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        targets_id=uuid4(),
        days=[DayPlan(day_index=0, meals=[comida], totals=macros)],
        config_version="test",
        prompt_version="test",
        model="engine-v1",
        input_hash="h",
        created_at=datetime(2026, 8, 13, tzinfo=UTC),
    )


def test_la_compra_marca_lo_que_ya_tienes_sin_borrarlo_de_la_lista(alimentos) -> None:
    """Borrar la línea escondería de dónde salen los gramos de la semana."""
    plan = _plan_de_una_comida(alimentos)
    catalogo = {f.id: f for f in alimentos.values()}
    arroz = alimentos["arroz blanco cocido"]

    grupos = shopping_list_for_plan(plan, catalogo, en_casa={arroz.id})
    lineas = {ln.name_es: ln for g in grupos for ln in g.lines}

    assert lineas["arroz blanco cocido"].ya_tengo is True
    assert lineas["arroz blanco cocido"].grams > 0
    assert lineas["pechuga de pollo"].ya_tengo is False
    assert sum(1 for ln in lineas.values() if not ln.ya_tengo) == 1
