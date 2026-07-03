"""Integración de repositorios: CRUD, seed idempotente y aislamiento de tenant."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from nutriplan.adapters.db.repositories import (
    SqlClientRepository,
    SqlFoodRepository,
    SqlJobRepository,
    SqlPlanRepository,
    SqlTargetsRepository,
)
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID, seed_local
from nutriplan.adapters.db.session import Base
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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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
        phase=PlanPhase.FIRST_15,
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
    assert loaded_plan == plan

    by_hash = await plan_repo.find_by_input_hash("hash-1")
    assert [p.id for p in by_hash] == [plan.id]

    await plan_repo.set_status(plan.id, PlanStatus.APPROVED)
    approved = await plan_repo.get(plan.id)
    assert approved.status == PlanStatus.APPROVED
    assert approved.approved_at is not None

    # otro tenant no ve el plan
    assert await SqlPlanRepository(session, OTHER_TENANT).get(plan.id) is None


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
