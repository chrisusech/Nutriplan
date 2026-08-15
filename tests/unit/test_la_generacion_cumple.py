"""Promesas de generación que ya existen: dislikes, variedad, otra semilla.

La matriz de perfiles y el check-in de kcal viven en sus archivos. Aquí se
clava lo que el motor de la casa no puede olvidar al regenerar.
"""

from pathlib import Path

from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.adapters.llm.template_selector import TemplateSelector
from nutriplan.adapters.meals.template_store import load_meal_catalog
from nutriplan.application.food_pool import matches_dislike
from nutriplan.domain.models import MacroTargets, MealSlot

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ROOT / "data" / "meals" / "food_classes.yaml"
TEMPLATES = ROOT / "data" / "meals" / "meal_templates.yaml"
DAILY = MacroTargets(kcal=2200, protein_g=150.0, carb_g=230.0, fat_g=70.0)


def _selector(seed: int) -> TemplateSelector:
    catalog = load_meal_catalog(CLASSES, TEMPLATES)
    foods = [f for f in catalog_by_name().values() if not matches_dislike(f, {"tilapia"})]
    return TemplateSelector(foods, catalog, DAILY, seed=seed)


def test_un_dislike_no_aparece_en_cuatro_semanas_seguidas() -> None:
    for seed in range(4):
        semana = _selector(seed).select_week(seed=seed)
        nombres = [food.name_es.lower() for dia in semana for d in dia.values() for food in d.foods]
        assert not any("tilapia" in n for n in nombres)


def test_generar_otra_no_clona_la_semana_anterior() -> None:
    a = _selector(0).select_week(seed=0)
    b = _selector(1).select_week(seed=1)
    cenas_a = tuple(dia[MealSlot.DINNER].name for dia in a)
    cenas_b = tuple(dia[MealSlot.DINNER].name for dia in b)
    assert cenas_a != cenas_b


def test_en_siete_dias_hay_al_menos_cuatro_cenas_distintas() -> None:
    semana = _selector(3).select_week(seed=3)
    cenas = {dia[MealSlot.DINNER].template_id for dia in semana}
    assert len(cenas) >= 4
