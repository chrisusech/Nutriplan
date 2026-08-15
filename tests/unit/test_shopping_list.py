"""La lista de compra se arma sumando los gramos del plan, no inventando."""

from datetime import UTC, datetime
from uuid import uuid4

from nutriplan.application.shopping_list import shopping_list_for_plan
from nutriplan.domain.models import (
    DayPlan,
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
    PlanCycle,
    UnitGranularity,
)


def _food(
    name: str,
    *,
    category: FoodCategory = FoodCategory.PROTEIN,
    free: bool = False,
    unit_g: float | None = None,
    unit_name: str | None = None,
    granularity: UnitGranularity = UnitGranularity.GRAMS,
) -> FoodItem:
    return FoodItem(
        id=uuid4(),
        name_es=name,
        category=category,
        kcal_100g=100,
        protein_100g=10,
        carb_100g=10,
        fat_100g=2,
        source="test",
        is_free=free,
        default_unit_g=unit_g,
        unit_name=unit_name,
        unit_granularity=granularity,
    )


def _plan(meals: list[MealEntry]) -> PlanCycle:
    zeros = MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0)
    days = [
        DayPlan(
            day_index=0,
            meals=meals,
            totals=MacroTargets(kcal=1, protein_g=1, carb_g=1, fat_g=1),
        ),
        *[DayPlan(day_index=i, meals=[], totals=zeros) for i in range(1, 7)],
    ]
    return PlanCycle(
        id=uuid4(),
        tenant_id=uuid4(),
        client_id=uuid4(),
        targets_id=uuid4(),
        days=days,
        config_version="t",
        prompt_version="t",
        model="engine-v1",
        input_hash="h",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_suma_gramos_del_mismo_alimento_en_la_semana() -> None:
    pollo = _food("pechuga de pollo")
    arroz = _food("arroz", category=FoodCategory.CARB)
    catalog = {pollo.id: pollo, arroz.id: arroz}
    meals = [
        MealEntry(
            slot=MealSlot.LUNCH,
            items=[
                MealItem(food_id=pollo.id, grams=150, position=0),
                MealItem(food_id=arroz.id, grams=200, position=1),
            ],
            computed=MacroTargets(kcal=400, protein_g=40, carb_g=50, fat_g=5),
        ),
        MealEntry(
            slot=MealSlot.DINNER,
            items=[
                MealItem(food_id=pollo.id, grams=120, position=0),
                MealItem(food_id=arroz.id, grams=100, position=1),
            ],
            computed=MacroTargets(kcal=300, protein_g=30, carb_g=25, fat_g=4),
        ),
    ]
    groups = shopping_list_for_plan(_plan(meals), catalog)
    flat = {ln.name_es: ln.grams for g in groups for ln in g.lines}
    assert flat["pechuga de pollo"] == 270.0
    assert flat["arroz"] == 300.0


def test_excluye_comida_libre_y_alimentos_libres() -> None:
    pollo = _food("pollo")
    lechuga = _food("lechuga", category=FoodCategory.VEGETABLE, free=True)
    catalog = {pollo.id: pollo, lechuga.id: lechuga}
    meals = [
        MealEntry(
            slot=MealSlot.LUNCH,
            items=[
                MealItem(food_id=pollo.id, grams=150, position=0),
                MealItem(food_id=lechuga.id, grams=50, position=1),
            ],
            computed=MacroTargets(kcal=250, protein_g=40, carb_g=0, fat_g=5),
            free_salad=True,
        ),
        MealEntry(
            slot=MealSlot.DINNER,
            items=[],
            computed=MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
            is_free_meal=True,
        ),
    ]
    groups = shopping_list_for_plan(_plan(meals), catalog)
    names = {ln.name_es for g in groups for ln in g.lines}
    assert names == {"pollo"}


def test_huevos_contables_llevan_etiqueta_de_unidades() -> None:
    huevo = _food(
        "huevo entero",
        unit_g=50,
        unit_name="huevo",
        granularity=UnitGranularity.WHOLE,
    )
    catalog = {huevo.id: huevo}
    meals = [
        MealEntry(
            slot=MealSlot.BREAKFAST,
            items=[MealItem(food_id=huevo.id, grams=150, position=0)],
            computed=MacroTargets(kcal=200, protein_g=18, carb_g=1, fat_g=14),
        )
    ]
    groups = shopping_list_for_plan(_plan(meals), catalog)
    line = groups[0].lines[0]
    assert line.grams == 150.0
    assert line.unit_label == "3 huevos"
