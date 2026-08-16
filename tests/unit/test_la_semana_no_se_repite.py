"""La semana tiene que leerse distinta cada día.

El beta salió con 27 cenas del mismo plato contra 1, y con cinco almuerzos
titulados igual —"Proteína con carbohidrato y aguacate"— aunque debajo cambiara
la carne. Las dos cosas se veían en la pantalla como "esta app me pone siempre lo
mismo", que es la queja que mata una app de menús. Estos tests son el freno.
"""

from collections import Counter
from pathlib import Path
from uuid import uuid4

import pytest
from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.adapters.llm.template_selector import TemplateSelector
from nutriplan.adapters.meals.template_store import load_meal_catalog
from nutriplan.domain.meal_template import Component, MealTemplate, dish_name
from nutriplan.domain.models import FoodCategory, FoodItem, MacroTargets, MealSlot

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ROOT / "data" / "meals" / "food_classes.yaml"
TEMPLATES = ROOT / "data" / "meals" / "meal_templates.yaml"
DAILY = MacroTargets(kcal=2200, protein_g=150.0, carb_g=230.0, fat_g=70.0)
PRINCIPALES = (MealSlot.LUNCH, MealSlot.DINNER)


@pytest.fixture(scope="module")
def semana():
    catalog = load_meal_catalog(CLASSES, TEMPLATES)
    foods = list(catalog_by_name().values())
    selector = TemplateSelector(foods, catalog, DAILY, seed=7)
    return selector.select_week(seed=7)


def test_la_cena_no_es_siempre_el_mismo_plato(semana) -> None:
    plantillas = {dia[MealSlot.DINNER].template_id for dia in semana}
    assert len(plantillas) >= 4, f"solo {len(plantillas)} formas de cenar en siete días"


def test_ningun_titulo_se_repite_dos_veces_en_las_comidas_grandes(semana) -> None:
    titulos = Counter(dia[slot].name for dia in semana for slot in PRINCIPALES)
    repetidos = {t: n for t, n in titulos.items() if n > 1}
    assert not repetidos, f"la semana se lee repetida: {repetidos}"


def test_el_plato_se_llama_por_lo_que_lleva_y_no_por_su_molde(semana) -> None:
    """«Proteína con carbohidrato» no es la cena de nadie."""
    for dia in semana:
        for slot in PRINCIPALES:
            nombre = dia[slot].name.lower()
            assert "proteína con" not in nombre
            assert "carbohidrato" not in nombre
            assert dia[slot].anchor.name_es.lower() in nombre


def _food(nombre: str, categoria: FoodCategory) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        source="curated",
        name_es=nombre,
        category=categoria,
        kcal_100g=100,
        protein_100g=10,
        carb_100g=10,
        fat_100g=1,
    )


def test_si_falta_la_grasa_opcional_el_nombre_no_la_promete() -> None:
    plantilla = MealTemplate(
        id="prueba",
        name="Molde",
        naming="{protein} con {carb} y {fat}",
        slots=(MealSlot.DINNER,),
        components=(
            Component(role=FoodCategory.PROTEIN, selector="@x"),
            Component(role=FoodCategory.CARB, selector="@y"),
            Component(role=FoodCategory.FAT, selector="@z", optional=True),
        ),
    )
    pollo = _food("pechuga de pollo", FoodCategory.PROTEIN)
    arroz = _food("arroz blanco cocido", FoodCategory.CARB)

    assert dish_name(plantilla, [pollo, arroz, None]) == "Pechuga de pollo con arroz blanco"


def test_el_titulo_no_copia_el_empaque_del_catalogo() -> None:
    from nutriplan.domain.meal_template import is_inedible_title, kitchen_name

    assert kitchen_name("atún en agua") == "atún en agua"
    assert kitchen_name("plátano maduro cocido") == "plátano maduro"
    assert is_inedible_title("Sopa de atún en agua con yuca")
    assert is_inedible_title("Sopa de plátano maduro cocido con carne")
    assert not is_inedible_title("Sudado de pollo con yuca")
