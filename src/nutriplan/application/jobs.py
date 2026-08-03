"""Jobs persistidos (ADR-06, sección 14): queued → running → done|failed.

Nivel 1: la ejecución es en proceso (asyncio), pero el estado ya vive en la
tabla generation_jobs — la UI hace polling del estado y el salto a una cola
real (Nivel 2) no cambia este contrato.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from nutriplan.application.dish_recipes import DishRecipeRepository, recipes_for_week
from nutriplan.application.generate_plan import generate_plan_for_client
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.meal_template import MealCatalog, static_recipes
from nutriplan.domain.models import PlanCycle
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.job_repository import Job, JobStatus, JobStatusSink
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.repository import ClientRepository, PlanRepository, TargetsRepository

logger = structlog.get_logger(__name__)

# Un job corre en el mismo proceso que sirve las peticiones: si ese proceso se
# cae o se reinicia a mitad, nadie vuelve a tocar la fila y se queda en
# `running` para siempre. Pasado este margen lo damos por muerto, que es mejor
# que dejar a alguien mirando la pantalla de carga sin salida.
JOB_STALE_AFTER = timedelta(minutes=5)


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


async def _finish(job: Job, job_repo: JobStatusSink, **updates: object) -> Job:
    job = job.model_copy(update={**updates, "updated_at": datetime.now(UTC)})
    await job_repo.update(job)
    return job


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
    prompts_dir: Path,
    model: str,
    variant: int = 0,
    catalog: MealCatalog | None = None,
    recipe_repo: DishRecipeRepository | None = None,
    commit: Callable[[], Awaitable[None]] | None = None,
    select_foods: bool = False,
    refine_names: bool = False,
    recipe_model: str | None = None,
) -> Job:
    """Genera el menú y deja el job en `done` o `failed`.

    `commit` confirma el plan antes de anunciarlo: quien vea `done` tiene que
    poder leer el plan, no encontrarse una transacción a medias.
    """
    job = await _finish(job, job_repo, status=JobStatus.RUNNING)
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
        cycle = await generate_plan_for_client(
            client=client,
            targets=targets,
            food_repo=food_repo,
            plan_repo=plan_repo,
            client_repo=client_repo,
            config=config,
            llm=llm,
            prompts_dir=prompts_dir,
            model=model,
            variant=variant,
            catalog=catalog,
            select_foods=select_foods,
            refine_names=refine_names,
        )
        # Las recetas van después del plan y nunca lo tumban: si fallan, la app
        # muestra los ingredientes y ya.
        if recipe_repo is not None:
            await _resolve_recipes(
                cycle=cycle, food_repo=food_repo, repo=recipe_repo, llm=llm,
                prompts_dir=prompts_dir, model=recipe_model or model, catalog=catalog,
            )
        if commit is not None:
            await commit()
        return await _finish(
            job,
            job_repo,
            status=JobStatus.DONE,
            result_id=cycle.id,
            input_hash=cycle.input_hash,
        )
    except Exception as exc:
        logger.warning("generation_job_failed", job_id=str(job.id), error=str(exc))
        return await _finish(job, job_repo, status=JobStatus.FAILED, error=str(exc))


async def _resolve_recipes(
    *,
    cycle: PlanCycle,
    food_repo: FoodRepository,
    repo: DishRecipeRepository,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
    catalog: MealCatalog | None,
) -> None:
    meals = [m for d in cycle.days for m in d.meals]
    ids = sorted({i.food_id for m in meals for i in m.items if i.food_id}, key=str)
    if not ids:
        return
    foods = {f.id: f for f in await food_repo.get_by_ids(ids)}
    try:
        await recipes_for_week(
            meals=meals, catalog=foods, repo=repo, llm=llm,
            prompts_dir=prompts_dir, model=model,
            static=static_recipes(catalog) if catalog else None,
        )
    except Exception as exc:  # noqa: BLE001 - una receta que falta no es un fallo
        logger.warning("dish_recipes_failed", plan_id=str(cycle.id), error=str(exc))
