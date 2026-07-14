"""Integración de repositorios: CRUD, seed idempotente y aislamiento de tenant."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nutriplan.adapters.db.migrate import upgrade_to_head_async
from nutriplan.adapters.db.repositories import (
    SqlClientRepository,
    SqlFoodRepository,
    SqlJobRepository,
    SqlPlanRepository,
    SqlTargetsRepository,
)
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID, seed_local
from nutriplan.domain.errors import TenantIsolationError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    DayPlan,
    FoodCategory,
    FoodItem,
    Goal,
    MacroTargets,
    MealEntry,
    MealFoodPortion,
    MealItem,
    MealSlot,
    PlanCycle,
    PlanPhase,
    PlanStatus,
    Sex,
)
from nutriplan.ports.job_repository import Job, JobKind, JobStatus

CSV_PATH = Path(__file__).resolve().parents[2] / "data" / "foods" / "curated_foods.csv"
OTHER_TENANT = uuid4()


@pytest.fixture
async def session_factory(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    await upgrade_to_head_async(url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def session(session_factory):
    async with session_factory() as s:
        yield s
        await s.commit()


def make_client(tenant_id=DEFAULT_TENANT_ID, **overrides) -> Client:
    base = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        name="Cliente Uno",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
        restrictions=["no_seafood"],
    )
    base.update(overrides)
    return Client(**base)


async def test_seed_is_idempotent(session) -> None:
    await seed_local(session, CSV_PATH)
    await seed_local(session, CSV_PATH)  # segunda corrida no duplica
    foods = await SqlFoodRepository(session, DEFAULT_TENANT_ID).list_universe()
    assert len(foods) >= 60
    assert all(f.tenant_id is None for f in foods)


async def test_client_roundtrip_with_preferences(session) -> None:
    await seed_local(session, CSV_PATH)
    foods = await SqlFoodRepository(session, DEFAULT_TENANT_ID).list_universe()
    liked = [f.id for f in foods[:5]]
    client = make_client(liked_food_ids=liked)

    repo = SqlClientRepository(session, DEFAULT_TENANT_ID)
    await repo.add(client)
    fetched = await repo.get(client.id)
    assert fetched is not None
    assert sorted(fetched.liked_food_ids) == sorted(liked)
    assert fetched.restrictions == ["no_seafood"]

    fetched.weight_kg = 60.0
    await repo.update(fetched)
    again = await repo.get(client.id)
    assert again.weight_kg == 60.0


async def test_tenant_isolation_clients(session) -> None:
    repo_a = SqlClientRepository(session, DEFAULT_TENANT_ID)
    repo_b = SqlClientRepository(session, OTHER_TENANT)

    client_a = make_client()
    await repo_a.add(client_a)

    # B no ve ni puede tocar los datos de A — por construcción
    assert await repo_b.get(client_a.id) is None
    assert await repo_b.list() == []
    with pytest.raises(TenantIsolationError):
        await repo_b.update(client_a)
    # y A no puede insertar entidades de otro tenant
    with pytest.raises(TenantIsolationError):
        await repo_a.add(make_client(tenant_id=OTHER_TENANT))


async def test_food_custom_is_tenant_scoped(session) -> None:
    await seed_local(session, CSV_PATH)
    repo_a = SqlFoodRepository(session, DEFAULT_TENANT_ID)
    repo_b = SqlFoodRepository(session, OTHER_TENANT)

    custom = FoodItem(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        source="custom",
        name_es="arepa de la abuela",
        category=FoodCategory.CARB,
        kcal_100g=210,
        protein_100g=5,
        carb_100g=43,
        fat_100g=2,
    )
    await repo_a.add_custom(custom)

    names_a = {f.name_es for f in await repo_a.list_universe()}
    names_b = {f.name_es for f in await repo_b.list_universe()}
    assert "arepa de la abuela" in names_a
    assert "arepa de la abuela" not in names_b  # global sí, custom ajeno no
    assert "pechuga de pollo" in names_b

    found = await repo_a.search("arepa")
    assert {f.name_es for f in found} >= {"arepa de la abuela", "arepa de maíz"}


async def test_targets_and_plan_roundtrip(session) -> None:
    await seed_local(session, CSV_PATH)
    client = make_client()
    await SqlClientRepository(session, DEFAULT_TENANT_ID).add(client)

    from tests.conftest import PROJECT_ROOT

    from nutriplan.adapters.config_yaml import YamlConfigProvider
    from nutriplan.domain.calculation import compute_targets

    config = YamlConfigProvider(
        PROJECT_ROOT / "config" / "nutrition.default.yaml"
    ).get_nutrition_config()
    targets = compute_targets(client, config)

    targets_repo = SqlTargetsRepository(session, DEFAULT_TENANT_ID)
    await targets_repo.add(targets)
    loaded = await targets_repo.latest_for_client(client.id)
    assert loaded is not None
    assert loaded.daily == targets.daily
    assert loaded.per_meal == targets.per_meal

    foods = await SqlFoodRepository(session, DEFAULT_TENANT_ID).list_universe()
    macro = MacroTargets(kcal=500, protein_g=40, carb_g=50, fat_g=15)
    plan = PlanCycle(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        client_id=client.id,
        targets_id=targets.id,
        days=[
            DayPlan(
                day_index=i,
                totals=macro,
                meals=[
                    MealEntry(
                        slot=MealSlot.LUNCH,
                        portions=[MealFoodPortion(food_id=foods[0].id, grams=120)],
                        computed=macro,
                        free_salad=True,
                    )
                ],
            )
            for i in range(7)
        ],
        config_version=config.version,
        prompt_version="plan_generation.v1",
        model="claude-sonnet-5",
        input_hash="hash-1",
        created_at=datetime.now(UTC),
    )
    plan_repo = SqlPlanRepository(session, DEFAULT_TENANT_ID)
    await plan_repo.add(plan)

    loaded_plan = await plan_repo.get(plan.id)
    assert loaded_plan is not None
    assert loaded_plan.input_hash == plan.input_hash
    assert len(loaded_plan.days) == 7
    meal = loaded_plan.days[0].meals[0]
    assert meal.slot == MealSlot.LUNCH
    assert meal.portions[0].food_id == foods[0].id
    assert meal.portions[0].grams == 120
    assert meal.free_salad is True
    assert meal.items[0].id is not None

    by_hash = await plan_repo.find_by_input_hash("hash-1")
    assert [p.id for p in by_hash] == [plan.id]

    await plan_repo.set_status(plan.id, PlanStatus.APPROVED)
    approved = await plan_repo.get(plan.id)
    assert approved.status == PlanStatus.APPROVED
    assert approved.approved_at is not None

    # otro tenant no ve el plan
    assert await SqlPlanRepository(session, OTHER_TENANT).get(plan.id) is None


async def test_meal_items_persist_with_stable_ids(session) -> None:
    """Las porciones viven en meal_items, no solo en el JSON legacy."""
    await seed_local(session, CSV_PATH)
    client = make_client()
    await SqlClientRepository(session, DEFAULT_TENANT_ID).add(client)

    from tests.conftest import PROJECT_ROOT

    from nutriplan.adapters.config_yaml import YamlConfigProvider
    from nutriplan.domain.calculation import compute_targets

    config = YamlConfigProvider(
        PROJECT_ROOT / "config" / "nutrition.default.yaml"
    ).get_nutrition_config()
    targets = compute_targets(client, config)
    await SqlTargetsRepository(session, DEFAULT_TENANT_ID).add(targets)

    foods = await SqlFoodRepository(session, DEFAULT_TENANT_ID).list_universe()
    macro = MacroTargets(kcal=500, protein_g=40, carb_g=50, fat_g=15)
    food_a, food_b = foods[0], foods[1]
    plan = PlanCycle(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        client_id=client.id,
        targets_id=targets.id,
        days=[
            DayPlan(
                day_index=0,
                meals=[
                    MealEntry(
                        slot=MealSlot.LUNCH,
                        items=[
                            MealItem(food_id=food_a.id, grams=120, position=0),
                            MealItem(food_id=food_b.id, grams=80, position=1),
                        ],
                        computed=macro,
                    )
                ],
                totals=macro,
            )
        ],
        config_version=config.version,
        prompt_version="plan_generation.v1",
        model="claude-sonnet-5",
        input_hash="hash-meal-items",
        created_at=datetime.now(UTC),
    )
    plan_repo = SqlPlanRepository(session, DEFAULT_TENANT_ID)
    await plan_repo.add(plan)

    loaded = await plan_repo.get(plan.id)
    assert loaded is not None
    meal = loaded.days[0].meals[0]
    assert len(meal.items) == 2
    assert meal.items[0].id is not None
    assert meal.portions[0].food_id == food_a.id
    assert meal.portions[1].grams == 80


async def test_update_day_preserves_matching_item_ids(session) -> None:
    """Cambiar gramos de un ítem no rota su id si el alimento es el mismo."""
    await seed_local(session, CSV_PATH)
    client = make_client()
    await SqlClientRepository(session, DEFAULT_TENANT_ID).add(client)

    from tests.conftest import PROJECT_ROOT

    from nutriplan.adapters.config_yaml import YamlConfigProvider
    from nutriplan.domain.calculation import compute_targets

    config = YamlConfigProvider(
        PROJECT_ROOT / "config" / "nutrition.default.yaml"
    ).get_nutrition_config()
    targets = compute_targets(client, config)
    await SqlTargetsRepository(session, DEFAULT_TENANT_ID).add(targets)

    foods = await SqlFoodRepository(session, DEFAULT_TENANT_ID).list_universe()
    macro = MacroTargets(kcal=500, protein_g=40, carb_g=50, fat_g=15)
    food = foods[0]
    plan = PlanCycle(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        client_id=client.id,
        targets_id=targets.id,
        days=[
            DayPlan(
                day_index=0,
                meals=[
                    MealEntry(
                        slot=MealSlot.LUNCH,
                        items=[MealItem(food_id=food.id, grams=120, position=0)],
                        computed=macro,
                    )
                ],
                totals=macro,
            )
        ],
        config_version=config.version,
        prompt_version="plan_generation.v1",
        model="claude-sonnet-5",
        input_hash="hash-update-day",
        created_at=datetime.now(UTC),
    )
    plan_repo = SqlPlanRepository(session, DEFAULT_TENANT_ID)
    await plan_repo.add(plan)
    before = (await plan_repo.get(plan.id)).days[0].meals[0].items[0].id

    updated_day = DayPlan(
        day_index=0,
        meals=[
            MealEntry(
                slot=MealSlot.LUNCH,
                items=[MealItem(food_id=food.id, grams=150, position=0, is_locked=True)],
                computed=macro,
            )
        ],
        totals=macro,
    )
    await plan_repo.update_day(plan.id, PlanPhase.FIRST_15, 0, updated_day, mark_edited=True)

    after = await plan_repo.get(plan.id)
    assert after is not None
    item = after.days[0].meals[0].items[0]
    assert item.id == before
    assert item.grams == 150
    assert item.is_locked is True
    assert after.edit_count == 1


async def test_edited_plan_not_reused_by_input_hash(session) -> None:
    """Un plan tocado a mano no debe devolverse al regenerar con el mismo hash."""
    await seed_local(session, CSV_PATH)
    client = make_client()
    await SqlClientRepository(session, DEFAULT_TENANT_ID).add(client)

    from tests.conftest import PROJECT_ROOT

    from nutriplan.adapters.config_yaml import YamlConfigProvider
    from nutriplan.domain.calculation import compute_targets

    config = YamlConfigProvider(
        PROJECT_ROOT / "config" / "nutrition.default.yaml"
    ).get_nutrition_config()
    targets = compute_targets(client, config)
    await SqlTargetsRepository(session, DEFAULT_TENANT_ID).add(targets)

    foods = await SqlFoodRepository(session, DEFAULT_TENANT_ID).list_universe()
    macro = MacroTargets(kcal=500, protein_g=40, carb_g=50, fat_g=15)
    plan = PlanCycle(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        client_id=client.id,
        targets_id=targets.id,
        days=[
            DayPlan(
                day_index=0,
                meals=[
                    MealEntry(
                        slot=MealSlot.LUNCH,
                        portions=[MealFoodPortion(food_id=foods[0].id, grams=120)],
                        computed=macro,
                    )
                ],
                totals=macro,
            )
        ],
        config_version=config.version,
        prompt_version="plan_generation.v1",
        model="claude-sonnet-5",
        input_hash="hash-edited",
        created_at=datetime.now(UTC),
    )
    plan_repo = SqlPlanRepository(session, DEFAULT_TENANT_ID)
    await plan_repo.add(plan)
    await plan_repo.mark_edited(plan.id)

    assert await plan_repo.find_by_input_hash("hash-edited") == []


async def test_job_repository_idempotency_key(session) -> None:
    repo = SqlJobRepository(session, DEFAULT_TENANT_ID)
    now = datetime.now(UTC)
    job = Job(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        kind=JobKind.GENERATE,
        idempotency_key="gen:cliente1:hash-1",
        created_at=now,
        updated_at=now,
    )
    await repo.add(job)

    found = await repo.get_by_idempotency_key("gen:cliente1:hash-1")
    assert found is not None and found.id == job.id
    assert found.status == JobStatus.QUEUED

    found.status = JobStatus.DONE
    found.result_id = uuid4()
    await repo.update(found)
    done = await repo.get(job.id)
    assert done.status == JobStatus.DONE

    # otro tenant no lo encuentra
    other = SqlJobRepository(session, OTHER_TENANT)
    assert await other.get_by_idempotency_key("gen:cliente1:hash-1") is None


async def test_thirty_day_plan_persists_both_phases(session) -> None:
    """30 días = 14 filas day_plans (7 × first_15 + 7 × next_15)."""
    from nutriplan.domain.models import PlanPhase

    await seed_local(session, CSV_PATH)
    foods = await SqlFoodRepository(session, DEFAULT_TENANT_ID).list_universe()
    macro = MacroTargets(kcal=500, protein_g=40, carb_g=50, fat_g=15)
    meal = MealEntry(
        slot=MealSlot.LUNCH,
        portions=[MealFoodPortion(food_id=foods[0].id, grams=120)],
        computed=macro,
    )
    days = [
        DayPlan(day_index=i, phase=PlanPhase.FIRST_15, totals=macro, meals=[meal])
        for i in range(7)
    ] + [
        DayPlan(day_index=i, phase=PlanPhase.NEXT_15, totals=macro, meals=[meal])
        for i in range(7)
    ]
    plan = PlanCycle(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        client_id=uuid4(),
        targets_id=uuid4(),
        duration_days=30,
        days=days,
        config_version="test",
        prompt_version="plan_generation.v1",
        model="test",
        input_hash="hash-30d",
        created_at=datetime.now(UTC),
    )
    plan_repo = SqlPlanRepository(session, DEFAULT_TENANT_ID)
    await plan_repo.add(plan)

    loaded = await plan_repo.get(plan.id)
    assert loaded is not None
    assert loaded.duration_days == 30
    assert len(loaded.days) == 14
    assert sum(1 for d in loaded.days if d.phase is PlanPhase.NEXT_15) == 7


async def test_only_one_plan_is_the_active_one_and_the_others_stay(session) -> None:
    """"Uno solo activo" lo garantiza la cardinalidad de la columna, no el código.

    Archivar es repuntar. Los planes anteriores NO se borran ni se marcan: siguen
    ahí, con su versión y sus macros, y se puede volver a cualquiera.
    """
    await seed_local(session, CSV_PATH)
    clients = SqlClientRepository(session, DEFAULT_TENANT_ID)
    client = make_client()
    await clients.add(client)
    assert (await clients.get(client.id)).active_plan_id is None

    from tests.conftest import PROJECT_ROOT

    from nutriplan.adapters.config_yaml import YamlConfigProvider
    from nutriplan.domain.calculation import compute_targets

    config = YamlConfigProvider(
        PROJECT_ROOT / "config" / "nutrition.default.yaml"
    ).get_nutrition_config()
    targets = compute_targets(client, config)
    await SqlTargetsRepository(session, DEFAULT_TENANT_ID).add(targets)

    # El peso con el que se calcularon viaja con ellos: es el seguimiento.
    stored = await SqlTargetsRepository(session, DEFAULT_TENANT_ID).get(targets.id)
    assert stored.weight_kg == client.weight_kg

    plans = SqlPlanRepository(session, DEFAULT_TENANT_ID)
    made = []
    for version in (1, 2, 3):
        cycle = PlanCycle(
            id=uuid4(),
            tenant_id=DEFAULT_TENANT_ID,
            client_id=client.id,
            targets_id=targets.id,
            days=[],
            version=version,
            variant=version - 1,
            config_version="test",
            prompt_version="v1",
            model="test",
            input_hash=f"hash-{version}",
            created_at=datetime.now(UTC),
        )
        await plans.add(cycle)
        await clients.set_active_plan(client.id, cycle.id)
        made.append(cycle)

    # El último activado es EL plan; los otros dos siguen existiendo.
    reloaded = await clients.get(client.id)
    assert reloaded.active_plan_id == made[2].id
    all_cycles = await plans.list_for_client(client.id)
    assert len(all_cycles) == 3
    assert sorted(c.version for c in all_cycles) == [1, 2, 3]

    # Y se puede volver a la v2: nada se perdió por el camino.
    await clients.set_active_plan(client.id, made[1].id)
    reloaded = await clients.get(client.id)
    assert reloaded.active_plan_id == made[1].id
    assert len(await plans.list_for_client(client.id)) == 3
