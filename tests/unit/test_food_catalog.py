"""Invariantes del catálogo curado.

La fuente de verdad de los alimentos es la base de datos; lo que se audita aquí
es el artefacto con el que se siembra, porque un typo que entre por ahí acaba en
la base y ya nadie lo revisa. El catálogo no solo lleva macros: decide en qué
comidas puede aparecer un alimento y a qué múltiplo redondea sus gramos, así que
una coma corrida no es un dato feo, es un plan roto para una clienta.
"""

from math import isclose

import pytest
from tests.fixtures.foods import catalog_foods

from nutriplan.adapters.food.catalog import CatalogEntry, write_jsonl
from nutriplan.domain.generation_rules import (
    CARB_GROUP,
    FAT_GROUP,
    PROTEIN_GROUP,
    SLOT_STRUCTURE,
)
from nutriplan.domain.models import (
    DEFAULT_SLOT_WEIGHT,
    MAX_SLOT_WEIGHT,
    FoodCategory,
    FoodItem,
    MealSlot,
    UnitGranularity,
)


@pytest.fixture(scope="module")
def catalog() -> list[FoodItem]:
    return catalog_foods()


def test_every_food_belongs_to_at_least_one_meal(catalog) -> None:
    """Un alimento sin slots es invisible para el generador: nunca se elegiría."""
    for food in catalog:
        assert food.meal_slots, f"{food.name_es}: sin meal_slots"


def test_free_foods_carry_no_macros_and_do_carry_a_label(catalog) -> None:
    for food in catalog:
        if food.is_free:
            assert food.category is FoodCategory.OTHER, food.name_es
            assert food.kcal_100g == 0, food.name_es
            assert food.protein_100g == food.carb_100g == food.fat_100g == 0, food.name_es
            assert food.free_text, f"{food.name_es}: libre pero sin texto que imprimir"
        else:
            assert food.free_text is None, f"{food.name_es}: no es libre pero trae free_text"


def test_fiber_is_a_subset_of_the_carbohydrate(catalog) -> None:
    """La fibra es carbohidrato no digerible: no puede haber más fibra que carbo.

    Es el detector de decimales corridos más barato que hay (14.5 vs 145).
    """
    for food in catalog:
        assert food.fiber_100g <= food.carb_100g + 1e-6, (
            f"{food.name_es}: fibra {food.fiber_100g} > carbo {food.carb_100g}"
        )


def test_macros_roughly_reconstruct_the_calories(catalog) -> None:
    """Atwater (4/4/9) como detector de typos, no como verdad exacta.

    La fibra y los alcoholes de azúcar hacen que el cuadre nunca sea perfecto, así
    que la tolerancia es amplia a propósito: aquí solo se busca la coma corrida.
    """
    for food in catalog:
        if food.is_free:
            continue
        atwater = food.protein_100g * 4 + food.carb_100g * 4 + food.fat_100g * 9
        assert isclose(atwater, food.kcal_100g, rel_tol=0.25), (
            f"{food.name_es}: kcal {food.kcal_100g} vs Atwater {atwater:.0f}"
        )


def test_portion_steps_are_consistent_with_the_unit(catalog) -> None:
    """Lo contable salta de unidad; lo que se pesa, de a poco.

    Si un alimento contable tuviera un paso que no es su unidad, el plan pediría
    '1.4 huevos'.
    """
    for food in catalog:
        assert food.portion_step_g > 0, food.name_es
        if food.unit_granularity is UnitGranularity.WHOLE and food.default_unit_g:
            assert food.portion_step_g == food.default_unit_g, (
                f"{food.name_es}: contable entero, pero el paso no es su unidad"
            )
        if food.unit_granularity is UnitGranularity.HALF and food.default_unit_g:
            assert food.portion_step_g == food.default_unit_g / 2, (
                f"{food.name_es}: admite medias unidades, pero el paso no es media unidad"
            )
        if food.portion_min_g is not None:
            assert food.portion_min_g >= food.portion_step_g, (
                f"{food.name_es}: piso {food.portion_min_g} bajo el paso {food.portion_step_g}"
            )


@pytest.mark.parametrize("slot", list(MealSlot))
def test_every_slot_can_be_filled_from_the_catalog(catalog, slot) -> None:
    """Cada comida necesita fuentes reales de lo que su estructura exige.

    Sin esto, el catálogo puede quedar sintácticamente perfecto y aun así ser
    imposible de porcionar — y el fallo aparecería en `generate_cycle`, cuatro
    capas más abajo y sin decir por qué.
    """
    rule = SLOT_STRUCTURE[slot]
    here = [f for f in catalog if slot in f.meal_slots and not f.is_free]

    if rule.requires_protein:
        assert [f for f in here if f.category in PROTEIN_GROUP], f"{slot.value}: sin proteína"
    if rule.requires_carb:
        group = {FoodCategory.FRUIT} if rule.fruit_as_carb else CARB_GROUP
        assert [f for f in here if f.category in group], f"{slot.value}: sin carbohidrato"
    if rule.allows_fat_item:
        assert [f for f in here if f.category in FAT_GROUP], f"{slot.value}: sin grasa"


def test_a_source_of_a_macro_actually_provides_that_macro(catalog) -> None:
    """El solver divide por la densidad del macro: un carbo con 0 g de carbo es
    una división por cero disfrazada (`GenerationError` en portioning.py)."""
    density = {
        FoodCategory.PROTEIN: "protein_100g",
        FoodCategory.CARB: "carb_100g",
        FoodCategory.FAT: "fat_100g",
        FoodCategory.FRUIT: "carb_100g",
        FoodCategory.DAIRY: "protein_100g",
    }
    for food in catalog:
        attr = density.get(food.category)
        if attr is None or food.is_free:
            continue
        assert getattr(food, attr) > 0, f"{food.name_es}: {food.category.value} sin {attr}"


def test_the_weight_of_a_slot_is_optional_and_bounded(tmp_path) -> None:
    """`almuerzo:3` declara cuánto encaja; `almuerzo` a secas vale lo de siempre.

    Es lo que permite que las 172 filas del catálogo sigan valiendo sin tocarlas: el
    peso es opcional y su ausencia significa DEFAULT_SLOT_WEIGHT.
    """
    entries = [
        CatalogEntry(
            fdc_id=1,
            name_es="con peso",
            category=FoodCategory.CARB,
            kcal_100g=100,
            protein_100g=1,
            carb_100g=20,
            fat_100g=0,
            meal_slots=[MealSlot.LUNCH, MealSlot.DINNER],
            slot_weights={MealSlot.LUNCH: 3, MealSlot.DINNER: 1},
        ),
        CatalogEntry(
            fdc_id=2,
            name_es="sin peso",
            category=FoodCategory.CARB,
            kcal_100g=100,
            protein_100g=1,
            carb_100g=20,
            fat_100g=0,
            meal_slots=[MealSlot.LUNCH, MealSlot.DINNER],
        ),
        CatalogEntry(
            fdc_id=3,
            name_es="peso absurdo",
            category=FoodCategory.CARB,
            kcal_100g=100,
            protein_100g=1,
            carb_100g=20,
            fat_100g=0,
            meal_slots=[MealSlot.LUNCH],
            slot_weights={MealSlot.LUNCH: 99},
        ),
    ]
    path = tmp_path / "catalogo.jsonl"
    write_jsonl(path, entries)
    foods = {f.name_es: f for f in catalog_foods(path)}

    assert foods["con peso"].weight_in(MealSlot.LUNCH) == 3
    assert foods["con peso"].weight_in(MealSlot.DINNER) == 1
    # Sin `:n` no hay entrada en el dict, y el default habla por el alimento.
    assert foods["sin peso"].slot_weights == {}
    assert foods["sin peso"].weight_in(MealSlot.LUNCH) == DEFAULT_SLOT_WEIGHT
    # Un dedo torcido no puede dominar el coste de todos los platos de la semana.
    assert foods["peso absurdo"].weight_in(MealSlot.LUNCH) == MAX_SLOT_WEIGHT
    # Y lo que no está declarado no vale nada, tenga el peso que tenga.
    assert foods["con peso"].weight_in(MealSlot.BREAKFAST) == 0


def test_the_main_meals_prefer_the_staple_carbs_over_the_bread(catalog) -> None:
    """El bug que originó todo esto: pan y arepa salían en almuerzos y cenas.

    No se prohíben —quien solo tiene arepa tiene que poder almorzar— pero pesan
    menos que el arroz, la papa o la quinoa, que es lo que se come a mediodía.
    """
    by_name = {f.name_es: f for f in catalog}
    staples = ["arroz blanco cocido", "papa cocida", "quinoa cocida", "plátano maduro cocido"]
    breads = ["pan integral", "arepa Sarys extradélgada", "tortilla de maíz"]

    for main in (MealSlot.LUNCH, MealSlot.DINNER):
        worst_staple = min(by_name[n].weight_in(main) for n in staples)
        best_bread = max(by_name[n].weight_in(main) for n in breads)
        assert worst_staple > best_bread, main.value

    # Y en el desayuno mandan ellos: es su comida.
    for name in breads:
        food = by_name[name]
        assert food.weight_in(MealSlot.BREAKFAST) == MAX_SLOT_WEIGHT
        assert food.weight_in(MealSlot.BREAKFAST) > food.weight_in(MealSlot.LUNCH)


def test_el_catalogo_habla_como_en_la_cocina(catalog) -> None:
    """Los nombres que ve la persona: sin jerga, con la marca de la arepa."""
    by_name = {f.name_es: f for f in catalog}
    assert "proteína en polvo" in by_name
    assert "whey" in by_name["proteína en polvo"].aliases
    assert "tofu" in by_name
    assert "tofu firme" not in by_name
    assert "ajo" in by_name
    assert "condimento" in by_name["ajo"].tags
    leche = by_name["leche deslactosada"]
    assert leche.category is FoodCategory.DAIRY
    assert "lacteo" in leche.tags
    assert leche.source == "derived"
    assert leche.source_ref == "171267"
    arepa = by_name["arepa Sarys extradélgada"]
    assert "arepa" in arepa.aliases
    assert "arepa de maíz" in arepa.aliases
    pita = by_name["pan pita integral"]
    assert pita.weight_in(MealSlot.BREAKFAST) == MAX_SLOT_WEIGHT
    assert pita.weight_in(MealSlot.LUNCH) == by_name["pan integral"].weight_in(MealSlot.LUNCH)
    for raro in (
        "frijol cargamanto",
        "cuscús cocido",
        "bulgur cocido",
        "cebada perlada cocida",
        "trigo sarraceno cocido",
        "amaranto cocido",
        "mijo cocido",
        "tempeh",
        "tahini",
        "huevo de codorniz",
    ):
        assert raro not in by_name, raro


def test_cada_ancla_usda_es_de_un_solo_alimento(catalog) -> None:
    """Dos filas no pueden reclamar el mismo fdc_id: el ancla deja de ser 1:1."""
    refs = [f.source_ref for f in catalog if f.source == "USDA" and f.source_ref]
    duplicados = [r for r in refs if refs.count(r) > 1]
    assert duplicados == [], f"source_ref USDA repetido: {sorted(set(duplicados))}"
