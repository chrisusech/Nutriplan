"""Jobs persistidos (ADR-06, sección 14): queued → running → done|failed.

Nivel 1: la ejecución es en proceso (asyncio), pero el estado ya vive en la
tabla generation_jobs — la UI hace polling del estado y el salto a una cola
real (Nivel 2) no cambia este contrato.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from nutriplan.application.dish_recipes import DishRecipeRepository, recipes_for_week
from nutriplan.application.generate_plan import generate_plan_for_client
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.meal_template import MealCatalog, static_recipes
from nutriplan.domain.models import PlanCycle
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.recipe_catalog import CuratedRecipe
from nutriplan.domain.taste import TasteProfile
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.job_repository import Job, JobPrefixCounter, JobStatus, JobStatusSink
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.plan_selector import OfflineEngineFactory
from nutriplan.ports.repository import ClientRepository, PlanRepository, TargetsRepository

logger = structlog.get_logger(__name__)

# Un job corre en el mismo proceso que sirve las peticiones: si ese proceso se
# cae o se reinicia a mitad, nadie vuelve a tocar la fila y se queda en
# `running` para siempre. Pasado este margen lo damos por muerto, que es mejor
# que dejar a alguien mirando la pantalla de carga sin salida.
JOB_STALE_AFTER = timedelta(minutes=5)
_HEARTBEAT_EVERY = timedelta(seconds=45)


def is_stale(job: Job, *, now: datetime | None = None) -> bool:
    """Un job que dice estar corriendo pero lleva demasiado sin dar señales."""
    if job.status is not JobStatus.RUNNING:
        return False
    updated = job.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - updated > JOB_STALE_AFTER


def new_job(*, tenant_id: UUID, idempotency_key: str) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=uuid4(),
        tenant_id=tenant_id,
        status=JobStatus.QUEUED,
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
    )


def generation_key_prefix(*, client_id: UUID, week_start: date) -> str:
    """La semana va DENTRO de la clave: es lo que permite contar después los
    intentos de esa semana sin depender de los planes, que se borran al rehacer."""
    return f"gen:{client_id}:{week_start.isoformat()}:"


def generation_key(*, client_id: UUID, week_start: date, input_hash: str) -> str:
    """La clave de idempotencia de una generación concreta."""
    return generation_key_prefix(client_id=client_id, week_start=week_start) + input_hash[:16]


class JobWeekGenerations:
    """Cuántas veces se pidió generar una semana, leído de los jobs.

    El formato de la clave vive aquí y no en el repositorio: la base de datos
    solo sabe contar filas que empiezan por un prefijo.
    """

    def __init__(self, jobs: JobPrefixCounter) -> None:
        self._jobs = jobs

    async def count_generations(self, client_id: UUID, week_start: date) -> int:
        return await self._jobs.count_for_prefix(
            generation_key_prefix(client_id=client_id, week_start=week_start)
        )


async def _finish(job: Job, job_repo: JobStatusSink, **updates: object) -> Job:
    job = job.model_copy(update={**updates, "updated_at": datetime.now(UTC)})
    await job_repo.update(job)
    return job


async def _heartbeat_loop(job_id: UUID, job_repo: JobStatusSink) -> None:
    """Mantiene `updated_at` vivo mientras genera — evita doble spawn en cluster."""
    try:
        while True:
            await asyncio.sleep(_HEARTBEAT_EVERY.total_seconds())
            await job_repo.touch(job_id)
    except asyncio.CancelledError:
        return


async def _stop(heartbeat: "asyncio.Task[None]") -> None:
    heartbeat.cancel()
    try:
        await heartbeat
    except asyncio.CancelledError:
        pass


async def run_generation_job(
    *,
    job: Job,
    job_repo: JobStatusSink,
    client_id: UUID,
    client_repo: ClientRepository,
    targets_repo: TargetsRepository,
    food_repo: FoodRepository,
    plan_repo: PlanRepository,
    config: NutritionConfig,
    llm: LLMClient | None,
    offline_engine: OfflineEngineFactory,
    prompts_dir: Path,
    model: str,
    variant: int = 0,
    catalog: MealCatalog | None = None,
    recipe_repo: DishRecipeRepository | None = None,
    commit: Callable[[], Awaitable[None]] | None = None,
    rollback: Callable[[], Awaitable[None]] | None = None,
    select_foods: bool = False,
    refine_names: bool = False,
    recipe_model: str | None = None,
    refine_model: str | None = None,
    eager_recipes: bool = False,
    select_max_retries: int | None = None,
    curated_recipes: list[CuratedRecipe] | None = None,
    week_start: date | None = None,
    taste: TasteProfile | None = None,
    activate: bool = True,
) -> Job:
    """Genera el menú y deja el job en `done` o `failed`.

    `commit` confirma el plan antes de anunciarlo: quien vea `done` tiene que
    poder leer el plan, no encontrarse una transacción a medias.
    `rollback` suelta el lock de SQLite antes de marcar el job fallido.
    """
    job = await _finish(job, job_repo, status=JobStatus.RUNNING)
    heartbeat = asyncio.create_task(_heartbeat_loop(job.id, job_repo))
    try:
        client = await client_repo.get(client_id)
        if client is None:
            raise GenerationError(f"Cliente {client_id} no existe")
        # El reparto, a las comidas que hace este cliente (puede comer 4, no 5).
        config = config.for_slots(client.meal_slots)
        targets = await targets_repo.latest_for_client(client_id)
        if targets is None:
            raise GenerationError(
                "El cliente no tiene macros calculados: calcula los objetivos primero"
            )
        # Cap de retries del camino IA: cada fallo = otro call y quema cuota.
        gen_config = config
        if select_foods and select_max_retries is not None:
            gen_config = config.model_copy(
                update={
                    "generation": config.generation.model_copy(
                        update={"max_retries": max(0, select_max_retries)}
                    )
                }
            )
        cycle = await generate_plan_for_client(
            client=client,
            targets=targets,
            food_repo=food_repo,
            plan_repo=plan_repo,
            client_repo=client_repo,
            config=gen_config,
            llm=llm,
            offline_engine=offline_engine,
            prompts_dir=prompts_dir,
            model=model,
            variant=variant,
            catalog=catalog,
            select_foods=select_foods,
            refine_names=refine_names,
            refine_model=refine_model,
            week_start=week_start,
            taste=taste,
            # La misma caché que escribe las recetas es la que las sugiere.
            library=recipe_repo,
            activate=activate,
        )
        # El menú se confirma YA: las recetas van bajo demanda (o en batch si
        # eager_recipes). Quien hace polling no debe esperar a cada plato.
        if commit is not None:
            await commit()
        # El latido se para ANTES de anunciar el final: las recetas de abajo
        # pueden tardar más que `_HEARTBEAT_EVERY`, y un latido a destiempo
        # dejaría la pantalla esperando un menú que ya está hecho.
        await _stop(heartbeat)
        job = await _finish(
            job,
            job_repo,
            status=JobStatus.DONE,
            result_id=cycle.id,
            input_hash=cycle.input_hash,
        )
        if eager_recipes and recipe_repo is not None:
            try:
                await _resolve_recipes(
                    cycle=cycle,
                    food_repo=food_repo,
                    repo=recipe_repo,
                    llm=llm,
                    prompts_dir=prompts_dir,
                    model=recipe_model or model,
                    catalog=catalog,
                    curated=curated_recipes,
                )
                if commit is not None:
                    await commit()
            except Exception as exc:  # noqa: BLE001 — el menú ya está
                logger.warning(
                    "dish_recipes_after_done_failed",
                    plan_id=str(cycle.id),
                    error=str(exc),
                )
        return job
    except Exception as exc:
        # Mensaje seguro para SQLite/logs (nada de romperse por un `…` en .env).
        err = str(exc).encode("utf-8", errors="replace").decode("utf-8")
        logger.warning("generation_job_failed", job_id=str(job.id), error=err)
        # Sin rollback, la sesión de generación sigue con la TX abortada y en
        # SQLite bloquea el beacon al escribir `failed` (database is locked).
        if rollback is not None:
            await rollback()
        await _stop(heartbeat)
        return await _finish(job, job_repo, status=JobStatus.FAILED, error=err)
    finally:
        await _stop(heartbeat)


async def _resolve_recipes(
    *,
    cycle: PlanCycle,
    food_repo: FoodRepository,
    repo: DishRecipeRepository,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
    catalog: MealCatalog | None,
    curated: list[CuratedRecipe] | None = None,
) -> None:
    meals = [m for d in cycle.days for m in d.meals]
    ids = sorted({i.food_id for m in meals for i in m.items if i.food_id}, key=str)
    if not ids:
        return
    foods = {f.id: f for f in await food_repo.get_by_ids(ids)}
    try:
        await recipes_for_week(
            meals=meals,
            catalog=foods,
            repo=repo,
            llm=llm,
            prompts_dir=prompts_dir,
            model=model,
            static=static_recipes(catalog) if catalog else None,
            curated=curated,
        )
    except Exception as exc:  # noqa: BLE001 - una receta que falta no es un fallo
        logger.warning("dish_recipes_failed", plan_id=str(cycle.id), error=str(exc))
