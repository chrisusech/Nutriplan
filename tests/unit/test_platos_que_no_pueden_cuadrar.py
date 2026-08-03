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


def test_un_desayuno_lacteo_con_pan_se_descarta_si_el_carbo_arrastra_proteina(
    nutrition_config,
) -> None:
    """El filtro optimista por kcal dejaba pasar requesón+pan+crema (~33 g vs 26)
    y el solver aterrizaba en ~41. Con la cuenta alineada al solver, ese plato
    desaparece cuando hay una alternativa que sí puede cuadrar."""
    requeson = _food(
        "queso cottage", FoodCategory.DAIRY, 98, 11.1, 3.4, 4.3, portion_min_g=100.0,
    )
    pan = _food(
        "pan integral", FoodCategory.CARB, 247, 12.4, 41.0, 3.4, portion_min_g=30.0,
    )
    crema = _food(
        "mantequilla de maní", FoodCategory.FAT, 588, 22.0, 20.0, 50.0, portion_min_g=10.0,
    )
    # Alternativa baja en proteína: fruta + carbo + grasa sin lácteo magro denso.
    avena = _food("avena", FoodCategory.CARB, 379, 13.0, 67.0, 6.5, portion_min_g=40.0)
    banano = _food("banano", FoodCategory.FRUIT, 89, 1.1, 22.8, 0.3, portion_min_g=80.0)
    aceite = _food("aceite de oliva", FoodCategory.FAT, 884, 0.0, 0.0, 100.0, portion_min_g=5.0)

    catalogo = MealCatalog(
        version="test",
        classes={
            "lacteo_magro": FoodClass(
                name="lacteo_magro", names_any=frozenset({requeson.name_es})
            ),
            "carbo_desayuno": FoodClass(
                name="carbo_desayuno", names_any=frozenset({pan.name_es, avena.name_es})
            ),
            "crema": FoodClass(
                name="crema", names_any=frozenset({crema.name_es, aceite.name_es})
            ),
            "fruta": FoodClass(name="fruta", names_any=frozenset({banano.name_es})),
        },
        templates=(
            MealTemplate(
                id="lacteo_carbo_crema",
                name="Requesón con pan",
                slots=(MealSlot.BREAKFAST,),
                components=(
                    Component(role=FoodCategory.PROTEIN, selector="@lacteo_magro"),
                    Component(role=FoodCategory.CARB, selector="@carbo_desayuno"),
                    Component(role=FoodCategory.FAT, selector="@crema"),
                ),
            ),
            MealTemplate(
                id="fruta_carbo_grasa",
                name="Avena con banano",
                slots=(MealSlot.BREAKFAST,),
                components=(
                    Component(role=FoodCategory.CARB, selector="@carbo_desayuno"),
                    Component(role=FoodCategory.FRUIT, selector="@fruta"),
                    Component(role=FoodCategory.FAT, selector="@crema"),
                ),
            ),
        ),
    )
    # Hombre 70 kg mantener ≈ desayuno 26 g prot / 124 g carb.
    diario = MacroTargets(kcal=2638, protein_g=117.7, carb_g=413.0, fat_g=70.3)
    # Solo desayuno: el selector exige pool en cada slot del config.
    selector = TemplateSelector(
        [requeson, pan, crema, avena, banano, aceite],
        catalogo,
        diario,
        config=nutrition_config.for_slots([MealSlot.BREAKFAST]),
    )
    # Los platos se identifican por alimentos: la misma plantilla expande
    # varias combinaciones (requesón+pan vs requesón+avena).
    pan_ids = {
        d.template_id
        for d in selector.pools[MealSlot.BREAKFAST]
        if any(f.name_es == "pan integral" for f in d.foods)
        and any(f.name_es == "queso cottage" for f in d.foods)
    }
    buenas = {d.template_id for d in selector.pools[MealSlot.BREAKFAST]}
    assert not pan_ids, f"debía filtrar requesón+pan, quedó {pan_ids}"
    assert "fruta_carbo_grasa" in buenas


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
