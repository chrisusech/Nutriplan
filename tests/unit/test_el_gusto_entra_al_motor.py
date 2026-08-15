"""El gusto entra al motor, no solo al prompt de la IA.

Calificar un plato 1 estrella dos veces tiene que vetarlo en la semana
siguiente aunque Gemini esté apagado. Sin esto, la queja del beta («siempre
lo mismo») no se puede cerrar.
"""

from pathlib import Path

from tests.fixtures.plan_builder import catalog_by_name

from nutriplan.adapters.llm.template_selector import TemplateSelector
from nutriplan.adapters.meals.template_store import load_meal_catalog
from nutriplan.domain.dish_recipe import dish_key
from nutriplan.domain.models import MacroTargets, MealSlot
from nutriplan.domain.taste import RatedDish, build_taste_profile

ROOT = Path(__file__).resolve().parents[2]
CLASSES = ROOT / "data" / "meals" / "food_classes.yaml"
TEMPLATES = ROOT / "data" / "meals" / "meal_templates.yaml"
DAILY = MacroTargets(kcal=2200, protein_g=150.0, carb_g=230.0, fat_g=70.0)


def _semana(seed: int = 7, taste=None):
    catalog = load_meal_catalog(CLASSES, TEMPLATES)
    foods = list(catalog_by_name().values())
    selector = TemplateSelector(foods, catalog, DAILY, seed=seed, taste=taste)
    return selector.select_week(seed=seed)


def test_un_plato_odiado_dos_veces_no_vuelve_en_el_menu() -> None:
    semana = _semana(seed=7)
    cena = semana[0][MealSlot.DINNER]
    clave = dish_key(cena.template_id, list(cena.food_ids))
    gusto = build_taste_profile(
        [
            RatedDish(cena.template_id, clave, cena.name, 1),
            RatedDish(cena.template_id, clave, cena.name, 2),
        ]
    )
    assert clave in gusto.rejected_keys
    siguiente = _semana(seed=7, taste=gusto)
    claves = {dish_key(d.template_id, list(d.food_ids)) for dia in siguiente for d in dia.values()}
    assert clave not in claves


def test_el_perfil_expone_las_claves_para_que_el_motor_las_lea() -> None:
    gusto = build_taste_profile(
        [RatedDish("t1", "k1", "Tilapia al horno", 1), RatedDish("t1", "k1", "Tilapia al horno", 1)]
    )
    assert gusto.rejected_keys == ["k1"]
    assert gusto.rejected_templates == ["t1"]
    assert not build_taste_profile([]).rejected_keys


def test_un_alimento_del_comentario_no_vuelve_en_el_menu() -> None:
    """avoid_food_ids es veto duro del pool, igual que un ban."""
    foods = catalog_by_name()
    pollo = next(f for f in foods.values() if "pechuga de pollo" in f.name_es.lower())
    gusto = build_taste_profile([], avoid_food_ids=[pollo.id])
    allowed = [f for f in foods.values() if f.id not in set(gusto.avoid_food_ids)]
    catalog = load_meal_catalog(CLASSES, TEMPLATES)
    selector = TemplateSelector(allowed, catalog, DAILY, seed=7, taste=gusto)
    semana = selector.select_week(seed=7)
    ids = {food.id for dia in semana for d in dia.values() for food in d.foods}
    assert pollo.id not in ids
