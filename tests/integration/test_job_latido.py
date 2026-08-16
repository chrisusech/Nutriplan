"""El latido del job no puede contradecir su resultado.

La generación anuncia `done` y solo después resuelve las recetas del batch. Ese
tramo puede durar más que un latido, y un latido a destiempo dejaba la pantalla
esperando un menú que ya estaba hecho.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.fixtures.foods import seed_foods

from nutriplan.adapters.db.migrate import upgrade_to_head_async
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.llm.offline_engine import build_offline_engine
from nutriplan.application import jobs as jobs_mod
from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.jobs import new_job, run_generation_job
from nutriplan.application.onboarding import create_profile
from nutriplan.container import Container
from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.models import ActivityLevel, Goal, MealSlot, Sex
from nutriplan.ports.job_repository import JobStatus

ROOT = Path(__file__).resolve().parents[2]
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
        yield container, container.repos(session), session
        await session.commit()
    await engine.dispose()


class _RecetasLentas:
    """Una biblioteca de recetas que tarda: es el tramo posterior al `done`."""

    def __init__(self, demora_s: float) -> None:
        self.demora_s = demora_s

    async def get_many(self, keys: list[str]) -> dict[str, DishRecipe]:
        await asyncio.sleep(self.demora_s)
        return {}

    async def add(self, recipe: DishRecipe, *, model: str, prompt_version: str) -> None:
        return None

    async def top_rated(
        self, *, min_rating: float, min_count: int, limit: int = 200
    ) -> list[DishRecipe]:
        return []


async def _perfil(container, repos, session):
    account = await container.auth_repo(session).create_account(
        tenant_id=DEFAULT_TENANT_ID,
        tenant_name="Latido",
        name="Latido",
        email="latido@example.com",
        password_hash="x",
    )
    # Una despensa realista: el motor necesita proteína, carbohidrato y fruta o
    # lácteo en cada slot, y coger «los primeros N» del catálogo no lo garantiza.
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
    catalog = [
        f
        for f in await repos.foods.list_universe()
        if f.name_es in gustos or any(alias in gustos for alias in f.aliases)
    ]
    return await create_profile(
        account_id=account.id,
        tenant_id=DEFAULT_TENANT_ID,
        name="Latido",
        sex=Sex.FEMALE,
        age_years=30,
        height_cm=165.0,
        weight_kg=62.0,
        goal=Goal.LOSE_FAT,
        activity_level=ActivityLevel.MODERATE,
        meal_slots=list(MealSlot),
        city="Medellín",
        country="CO",
        liked_food_ids=[f.id for f in catalog],
        client_repo=repos.clients,
    )


async def test_un_menu_que_tarda_en_recetas_sigue_marcado_como_listo(ctx, monkeypatch) -> None:
    """El menú ya está hecho: nadie puede decirle a la pantalla que siga esperando."""
    container, repos, session = ctx
    monkeypatch.setattr(jobs_mod, "_HEARTBEAT_EVERY", timedelta(seconds=0.05))

    client = await _perfil(container, repos, session)
    await compute_and_store_targets(
        client=client, config_provider=container.config_provider, targets_repo=repos.targets
    )
    job = new_job(tenant_id=DEFAULT_TENANT_ID, idempotency_key=f"gen:{client.id}")
    await repos.jobs.add(job)

    job = await run_generation_job(
        job=job,
        job_repo=repos.jobs,
        client_id=client.id,
        client_repo=repos.clients,
        targets_repo=repos.targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        config=container.config_provider.get_nutrition_config(),
        llm=None,
        offline_engine=build_offline_engine,
        prompts_dir=PROMPTS,
        model="offline-heuristic",
        catalog=container.meal_catalog,
        recipe_repo=_RecetasLentas(demora_s=0.4),
        eager_recipes=True,
    )

    assert job.status is JobStatus.DONE
    guardado = await repos.jobs.get(job.id)
    assert guardado.status is JobStatus.DONE
    assert guardado.result_id is not None


async def test_el_latido_no_resucita_un_job_terminado(ctx) -> None:
    """`touch` solo dice «sigo vivo», y solo si el job sigue corriendo."""
    _, repos, session = ctx
    job = new_job(tenant_id=DEFAULT_TENANT_ID, idempotency_key=f"gen:{uuid4()}")
    await repos.jobs.add(job)
    hecho = job.model_copy(update={"status": JobStatus.DONE, "result_id": uuid4()})
    await repos.jobs.update(hecho)

    antes = (await repos.jobs.get(job.id)).updated_at
    await repos.jobs.touch(job.id)
    despues = await repos.jobs.get(job.id)

    assert despues.status is JobStatus.DONE
    assert despues.result_id == hecho.result_id
    assert despues.updated_at == antes


async def test_un_job_corriendo_si_recibe_el_latido(ctx) -> None:
    """Y mientras corre sí tiene que refrescarse, o lo dan por huérfano."""
    _, repos, session = ctx
    viejo = datetime.now(UTC) - timedelta(minutes=3)
    job = new_job(tenant_id=DEFAULT_TENANT_ID, idempotency_key=f"gen:{uuid4()}")
    await repos.jobs.add(job)
    await repos.jobs.update(job.model_copy(update={"status": JobStatus.RUNNING}))

    await repos.jobs.touch(job.id)
    assert (await repos.jobs.get(job.id)).updated_at > viejo
