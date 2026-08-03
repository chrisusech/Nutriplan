"""Un plato que nunca podrá cuadrar no se ofrece.

Las kcal de una comida fijan cuánto se sirve, y con ello la proteína. Un yogur
griego en un snack de 129 kcal son ~22 g de proteína contra un objetivo de 5:
no hay gramaje que lo arregle. El motor emitía ese plato igual y la generación
moría cuatro intentos después con un error que no señalaba la causa.
"""

from uuid import uuid4

from nutriplan.adapters.llm.template_selector import TemplateSelector
from nutriplan.domain.meal_template import (
    Component,
    FoodClass,
    MealCatalog,
    MealTemplate,
)
from nutriplan.domain.models import FoodCategory, FoodItem, MacroTargets, MealSlot
from nutriplan.domain.nutrition_config import NutritionConfig


def _food(nombre, categoria, kcal, prot, carb, fat, **extra) -> FoodItem:
    return FoodItem(
        id=uuid4(), source="curated", name_es=nombre, category=categoria,
        kcal_100g=kcal, protein_100g=prot, carb_100g=carb, fat_100g=fat, **extra,
    )


# Un yogur griego de verdad: casi toda su energía es proteína.
YOGUR = _food("yogur griego natural", FoodCategory.DAIRY, 59, 10.0, 3.6, 0.4)
# Un banano de verdad: energía sin proteína.
BANANO = _food("banano", FoodCategory.FRUIT, 89, 1.1, 22.8, 0.3)


def _catalogo(*plantillas: MealTemplate) -> MealCatalog:
    return MealCatalog(
        version="test",
        classes={
            "lacteo_magro": FoodClass(
                name="lacteo_magro", names_any=frozenset({YOGUR.name_es})
            ),
            "fruta": FoodClass(name="fruta", names_any=frozenset({BANANO.name_es})),
        },
        templates=tuple(plantillas),
    )


def _selector(catalogo: MealCatalog, config: NutritionConfig) -> TemplateSelector:
    """Un día de mujer en déficit, recortado al snack: ~129 kcal y ~5 g de
    proteína. El resto de comidas no hacen falta para lo que se prueba."""
    diario = MacroTargets(kcal=1716, protein_g=99.2, carb_g=170.0, fat_g=57.0)
    return TemplateSelector(
        [YOGUR, BANANO], catalogo, diario, config=config.for_slots([MealSlot.SNACK_PM])
    )


def test_un_yogur_solo_no_se_ofrece_como_snack(nutrition_config) -> None:
    """Sus 129 kcal son 22 g de proteína contra un objetivo de 5."""
    solo_yogur = MealTemplate(
        id="lacteo_solo", name="Yogur griego",
        slots=(MealSlot.SNACK_PM, MealSlot.BREAKFAST),
        components=(Component(role=FoodCategory.PROTEIN, selector="@lacteo_magro"),),
    )
    con_fruta = MealTemplate(
        id="lacteo_fruta", name="Yogur griego con fruta",
        slots=(MealSlot.SNACK_PM,),
        components=(
            Component(role=FoodCategory.PROTEIN, selector="@lacteo_magro"),
            Component(role=FoodCategory.FRUIT, selector="@fruta"),
        ),
    )
    selector = _selector(_catalogo(solo_yogur, con_fruta), nutrition_config)

    ofrecidos = {d.template_id for d in selector.pools[MealSlot.SNACK_PM]}
    assert "lacteo_solo" not in ofrecidos
    # Y el que sí puede cuadrar sigue en pie: el banano absorbe las kcal.
    assert "lacteo_fruta" in ofrecidos


def test_si_ningun_plato_cuadra_el_slot_no_se_queda_vacio(nutrition_config) -> None:
    """Un plan difícil de cuadrar es mejor que ningún plan: el filtro se rinde
    antes que dejar a alguien sin comida."""
    imposible = MealTemplate(
        id="lacteo_solo", name="Yogur griego",
        slots=(MealSlot.SNACK_PM,),
        components=(Component(role=FoodCategory.PROTEIN, selector="@lacteo_magro"),),
    )
    selector = _selector(_catalogo(imposible), nutrition_config)
    assert selector.pools[MealSlot.SNACK_PM]
