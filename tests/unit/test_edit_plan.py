"""Edición quirúrgica: resolve, swap, remove, validación."""

from uuid import uuid4

import pytest
from tests.fixtures.plan_builder import build_fixed_plan, catalog_by_name

from nutriplan.application.edit_plan import (
    remove_food_from_slot,
    resolve_day,
    swap_food_in_slot,
    validate_swap_food,
)
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    MacroTargets,
    MealSlot,
    NutritionTargets,
    Sex,
)
from nutriplan.domain.nutrition_config import NutritionConfig


@pytest.fixture(scope="module")
def foods():
    return catalog_by_name()


@pytest.fixture(scope="module")
def plan_bundle():
    return build_fixed_plan()


@pytest.fixture
def targets(nutrition_config: NutritionConfig, plan_bundle) -> NutritionTargets:
    plan, _, _ = plan_bundle
    daily = MacroTargets(kcal=1700, protein_g=120, carb_g=180, fat_g=55)
    return NutritionTargets(
        id=uuid4(),
        tenant_id=plan.tenant_id,
        client_id=plan.client_id,
        daily=daily,
        per_meal={},
        config_version=nutrition_config.version,
        computed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )


@pytest.fixture
def client(foods, plan_bundle) -> Client:
    plan, food_map, _ = plan_bundle
    return Client(
        id=plan.client_id,
        tenant_id=plan.tenant_id,
        name="Test",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165,
        weight_kg=65,
        goal=Goal.MAINTAIN,
        activity_level=ActivityLevel.MODERATE,
        liked_food_ids=list(food_map.keys()),
        restrictions=[],
    )


class _FakeFoodRepo:
    def __init__(self, foods_map: dict) -> None:
        self._foods = foods_map

    async def get_by_ids(self, ids: list) -> list:
        return [self._foods[i] for i in ids if i in self._foods]


class _FakeClientRepo:
    def __init__(self, banned: set | None = None) -> None:
        self._banned = banned or set()

    async def list_banned_food_ids(self, client_id) -> list:  # noqa: ARG002
        return list(self._banned)


@pytest.mark.asyncio
async def test_resolve_day_after_gram_edit(plan_bundle, targets, nutrition_config) -> None:
    plan, foods, _ = plan_bundle
    day = plan.days[0]
    meal = next(m for m in day.meals if m.slot is MealSlot.BREAKFAST)
    item = meal.items[0]
    item.grams = (item.grams or 0) + 20
    item.is_locked = True

    resolved = await resolve_day(
        day, foods, targets, nutrition_config, edited_slot=MealSlot.BREAKFAST
    )
    assert resolved.totals.kcal > 0
    assert len(resolved.meals) == len(day.meals)


@pytest.mark.asyncio
async def test_swap_food_in_slot(plan_bundle, targets, nutrition_config, foods) -> None:
    plan, foods_map, _ = plan_bundle
    day = plan.days[0]
    meal = next(m for m in day.meals if m.slot is MealSlot.BREAKFAST)
    arepa = next(
        item for item in meal.items if foods_map[item.food_id].name_es == "arepa de maíz"
    )
    new_food = foods["avena en hojuelas"]

    swapped = await swap_food_in_slot(
        cycle=plan,
        phase=day.phase,
        day_index=day.day_index,
        slot=MealSlot.BREAKFAST,
        old_food_id=arepa.food_id,
        new_food=new_food,
        foods={**foods_map, new_food.id: new_food},
        targets=targets,
        config=nutrition_config,
    )
    breakfast = next(m for m in swapped.meals if m.slot is MealSlot.BREAKFAST)
    ids = {item.food_id for item in breakfast.items}
    assert new_food.id in ids
    assert arepa.food_id not in ids


@pytest.mark.asyncio
async def test_remove_food_from_slot(plan_bundle, targets, nutrition_config) -> None:
    plan, foods_map, _ = plan_bundle
    day = plan.days[0]
    meal = next(m for m in day.meals if m.slot is MealSlot.BREAKFAST)
    aguacate = next(
        item for item in meal.items if foods_map[item.food_id].name_es == "aguacate"
    )

    updated = await remove_food_from_slot(
        cycle=plan,
        phase=day.phase,
        day_index=day.day_index,
        slot=MealSlot.BREAKFAST,
        food_id=aguacate.food_id,
        foods=foods_map,
        targets=targets,
        config=nutrition_config,
    )
    breakfast = next(m for m in updated.meals if m.slot is MealSlot.BREAKFAST)
    assert aguacate.food_id not in {item.food_id for item in breakfast.items}


@pytest.mark.asyncio
async def test_validate_swap_rejects_banned(client, plan_bundle, foods) -> None:
    plan, foods_map, _ = plan_bundle
    new_food = foods["avena en hojuelas"]
    banned_id = new_food.id
    repo = _FakeFoodRepo(foods_map)
    client_repo = _FakeClientRepo(banned={banned_id})

    with pytest.raises(GenerationError, match="permitido"):
        await validate_swap_food(
            client=client,
            food_repo=repo,
            client_repo=client_repo,
            new_food=new_food,
            slot=MealSlot.BREAKFAST,
        )


@pytest.mark.asyncio
async def test_validate_swap_rejects_wrong_slot(client, plan_bundle, foods) -> None:
    _, foods_map, _ = plan_bundle
    # Aceite no encaja en desayuno según meal_slots típicos
    oil = foods["aceite de oliva"]
    repo = _FakeFoodRepo(foods_map)
    client_repo = _FakeClientRepo()

    with pytest.raises(GenerationError, match="encaja"):
        await validate_swap_food(
            client=client,
            food_repo=repo,
            client_repo=client_repo,
            new_food=oil,
            slot=MealSlot.BREAKFAST,
        )


@pytest.mark.asyncio
async def test_editing_a_day_does_not_swallow_its_free_meal(
    plan_bundle, targets, nutrition_config
) -> None:
    """`_solved_to_day` reconstruye el día desde el solver, y la comida libre NO
    está en el solver: no tiene alimentos que porcionar.

    Sin preservarla explícitamente, tocar los gramos de cualquier otra comida se la
    llevaba por delante — en silencio, y el plan perdía la única celda que el
    cliente busca al abrirlo.
    """
    plan, foods, _ = plan_bundle
    day = plan.days[0]

    # La cena de ese día pasa a ser comida libre.
    freed = [
        m.model_copy(update={
            "items": [],
            "computed": MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
            "is_free_meal": True,
        })
        if m.slot is MealSlot.DINNER else m
        for m in day.meals
    ]
    day = day.model_copy(update={"meals": freed})
    assert any(m.is_free_meal for m in day.meals)

    # Se edita OTRA comida (el desayuno): se re-porciona el día entero.
    resolved = await resolve_day(day, foods, targets, nutrition_config,
                                 edited_slot=MealSlot.BREAKFAST)

    free = [m for m in resolved.meals if m.is_free_meal]
    assert len(free) == 1, "la comida libre sigue ahí"
    assert free[0].slot is MealSlot.DINNER
    assert free[0].items == []
    # Y en su sitio del día, no al final.
    assert [m.slot for m in resolved.meals] == sorted(
        [m.slot for m in resolved.meals], key=lambda s: list(MealSlot).index(s)
    )
    # Las demás comidas siguen con sus alimentos.
    assert all(m.items for m in resolved.meals if not m.is_free_meal)


@pytest.mark.asyncio
async def test_the_free_meal_has_nothing_to_swap_or_remove(
    plan_bundle, targets, nutrition_config, foods
) -> None:
    """En la pantalla esa celda no ofrece controles, pero el endpoint es público."""
    plan, food_map, _ = plan_bundle
    day = plan.days[0]
    freed = [
        m.model_copy(update={
            "items": [],
            "computed": MacroTargets(kcal=0, protein_g=0, carb_g=0, fat_g=0),
            "is_free_meal": True,
        })
        if m.slot is MealSlot.DINNER else m
        for m in day.meals
    ]
    cycle = plan.model_copy(update={
        "days": [day.model_copy(update={"meals": freed}), *plan.days[1:]]
    })
    arroz = foods["arroz blanco cocido"]

    with pytest.raises(GenerationError, match="comida libre"):
        await swap_food_in_slot(
            cycle=cycle, phase=cycle.days[0].phase, day_index=0, slot=MealSlot.DINNER,
            old_food_id=arroz.id, new_food=arroz, foods=food_map,
            targets=targets, config=nutrition_config,
        )

    with pytest.raises(GenerationError, match="comida libre"):
        await remove_food_from_slot(
            cycle=cycle, phase=cycle.days[0].phase, day_index=0, slot=MealSlot.DINNER,
            food_id=arroz.id, foods=food_map, targets=targets, config=nutrition_config,
        )
