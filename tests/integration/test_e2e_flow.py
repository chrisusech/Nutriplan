"""Paso 10 end-to-end con mock LLM: Word → intake → targets → plan → PDF.

El flujo completo del negocio sin tocar la red: extracción con respuestas
grabadas, generación con el selector heurístico, jobs persistidos con su
ciclo de estados, aprobación humana y export renderizado de verdad.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.fixtures.foods import seed_foods

from nutriplan.adapters.db.migrate import upgrade_to_head_async
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.llm.offline_engine import build_offline_engine
from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.generate_plan import generate_plan_for_client
from nutriplan.application.jobs import new_job, run_generation_job
from nutriplan.application.onboarding import create_profile
from nutriplan.container import Container
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    MealSlot,
    PlanStatus,
    Sex,
)
from nutriplan.ports.job_repository import JobStatus

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "intakes"
PROMPTS = ROOT / "prompts"


@pytest.fixture
async def ctx(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/t.db"
    await upgrade_to_head_async(url)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    container = Container(tenant_id=DEFAULT_TENANT_ID)
    async with factory() as session:
        await seed_foods(session)
        yield container, container.repos(session), session, tmp_path
        await session.commit()
    await engine.dispose()


async def _perfil_de_ana(container, repos, session) -> Client:
    """Ana se describe a sí misma en el onboarding, no en un Word."""
    account = await container.auth_repo(session).create_account(
        tenant_id=DEFAULT_TENANT_ID,
        tenant_name="Ana Pérez",
        name="Ana Pérez",
        email="ana@example.com",
        password_hash="x",
    )
    # Los alimentos que Ana dijo que le gustan. Una lista realista y acotada:
    # este test es sobre el flujo completo, no sobre el tamaño del catálogo.
    gustos = {
        "pechuga de pollo",
        "huevo entero",
        "atún en agua",
        "arroz blanco",
        "arroz integral",
        "arepa Sarys extradélgada",
        "arepa de maíz",
        "papa",
        "avena en hojuelas",
        "aguacate",
        "maní natural",
        "banano",
        "fresa",
        "mango",
        "brócoli",
        "espinaca",
        "tomate",
        "yogur griego natural",
    }
    catalog = await repos.foods.list_universe()
    liked = [
        f.id for f in catalog if f.name_es in gustos or any(alias in gustos for alias in f.aliases)
    ]
    return await create_profile(
        account_id=account.id,
        tenant_id=DEFAULT_TENANT_ID,
        name="Ana Pérez",
        sex=Sex.FEMALE,
        age_years=28,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
        meal_slots=list(MealSlot),
        city="Medellín",
        country="CO",
        liked_food_ids=liked,
        restrictions=["no_seafood"],
        eating_pattern_raw="Desayuno rápido, almuerzo en la oficina, entreno de noche.",
        context_tags=["entrena_noche", "come_rapido"],
        client_repo=repos.clients,
    )


async def test_del_onboarding_al_menu_aprobado(ctx) -> None:
    container, repos, session, tmp_path = ctx

    # 1) La persona se describe en el onboarding → perfil persistido
    client = await _perfil_de_ana(container, repos, session)

    # 3) Macros calculados y persistidos
    targets = await compute_and_store_targets(
        client=client, config_provider=container.config_provider, targets_repo=repos.targets
    )
    assert targets.daily.kcal > 1000

    # 4) Generación como job persistido (modo offline → heurístico)
    job = new_job(tenant_id=DEFAULT_TENANT_ID, idempotency_key=f"gen:{client.id}")
    await repos.jobs.add(job)
    config = container.config_provider.get_nutrition_config()
    job = await run_generation_job(
        job=job,
        job_repo=repos.jobs,
        client_id=client.id,
        client_repo=repos.clients,
        targets_repo=repos.targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        config=config,
        llm=None,
        offline_engine=build_offline_engine,
        prompts_dir=PROMPTS,
        model="offline-heuristic",
    )
    assert job.status == JobStatus.DONE, job.error
    cycles = await repos.plans.list_for_client(client.id)
    assert len(cycles) == 1  # una sola semana, no 15+15
    first = cycles[0]
    assert len(first.days) == 7

    # 5) Reproducibilidad: regenerar devuelve el MISMO plan sin re-generar
    again = await generate_plan_for_client(
        client=client,
        targets=targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        client_repo=repos.clients,
        config=config,
        llm=None,
        offline_engine=build_offline_engine,
        prompts_dir=PROMPTS,
        model="offline-heuristic",
    )
    assert again.id == first.id

    # 6) El menú queda como borrador activo: en la app del usuario no hay
    #    aprobación de nadie, el menú es suyo desde que se genera.
    assert first.status == PlanStatus.DRAFT
    assert (await repos.clients.get(client.id)).active_plan_id == first.id


async def test_quien_marca_comida_libre_igual_recibe_siete_dias_completos(
    ctx,
) -> None:
    """La comida libre se eliminó: el domingo cena también es un plato de casa."""
    container, repos, session, _ = ctx
    foods = await repos.foods.list_universe()
    client = Client(
        id=uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        user_id=uuid4(),
        name="Sin libre",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165,
        weight_kg=65,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
        liked_food_ids=[f.id for f in foods],
        restrictions=[],
        free_meal_day=6,
        free_meal_slot=MealSlot.DINNER,
    )
    await repos.clients.add(client)
    targets = await compute_and_store_targets(
        client=client, config_provider=container.config_provider, targets_repo=repos.targets
    )
    cycle = await generate_plan_for_client(
        client=client,
        targets=targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        client_repo=repos.clients,
        config=container.config_provider.get_nutrition_config(),
        llm=None,
        offline_engine=build_offline_engine,
        prompts_dir=PROMPTS,
        model="offline-heuristic",
    )
    assert not any(m.is_free_meal for d in cycle.days for m in d.meals)
    assert all(len(d.meals) == 5 for d in cycle.days)
    sunday = next(d for d in cycle.days if d.day_index == 6)
    cena = next(m for m in sunday.meals if m.slot is MealSlot.DINNER)
    assert cena.items
    assert cena.computed.kcal > 0
