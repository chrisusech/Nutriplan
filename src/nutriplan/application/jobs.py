"""Jobs persistidos (ADR-06, sección 14): queued → running → done|failed.

Nivel 1: la ejecución es en proceso (asyncio), pero el estado ya vive en la
tabla generation_jobs — la UI hace polling del estado y el salto a una cola
real (Nivel 2) no cambia este contrato.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import structlog

from nutriplan.application.export_plan import export_plan
from nutriplan.application.generate_plan import generate_plan_for_client
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.models import Branding
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.job_repository import (
    ArtifactRepository,
    Job,
    JobKind,
    JobRepository,
    JobStatus,
)
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.renderer import Renderer
from nutriplan.ports.repository import ClientRepository, PlanRepository, TargetsRepository

logger = structlog.get_logger(__name__)


def new_job(*, tenant_id: UUID, kind: JobKind, idempotency_key: str) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=uuid4(),
        tenant_id=tenant_id,
        kind=kind,
        status=JobStatus.QUEUED,
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
    )


async def _finish(job: Job, job_repo: JobRepository, **updates: object) -> Job:
    job = job.model_copy(update={**updates, "updated_at": datetime.now(UTC)})
    await job_repo.update(job)
    return job


async def run_generation_job(
    *,
    job: Job,
    job_repo: JobRepository,
    client_id: UUID,
    client_repo: ClientRepository,
    targets_repo: TargetsRepository,
    food_repo: FoodRepository,
    plan_repo: PlanRepository,
    config: NutritionConfig,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
) -> Job:
    job = await _finish(job, job_repo, status=JobStatus.RUNNING)
    try:
        client = await client_repo.get(client_id)
        if client is None:
            raise GenerationError(f"Cliente {client_id} no existe")
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
            config=config,
            llm=llm,
            prompts_dir=prompts_dir,
            model=model,
        )
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


async def run_export_job(
    *,
    job: Job,
    job_repo: JobRepository,
    plan_id: UUID,
    fmt: str,
    plan_repo: PlanRepository,
    food_repo: FoodRepository,
    artifact_repo: ArtifactRepository,
    renderer: Renderer,
    branding: Branding,
    exports_dir: Path,
) -> Job:
    job = await _finish(job, job_repo, status=JobStatus.RUNNING)
    try:
        if fmt not in ("pdf", "docx"):
            raise ValueError(f"Formato no soportado: {fmt}")
        artifact, _ = await export_plan(
            plan_id=plan_id,
            fmt=fmt,  # type: ignore[arg-type]
            plan_repo=plan_repo,
            food_repo=food_repo,
            artifact_repo=artifact_repo,
            renderer=renderer,
            branding=branding,
            exports_dir=exports_dir,
        )
        return await _finish(job, job_repo, status=JobStatus.DONE, result_id=artifact.id)
    except Exception as exc:
        logger.warning("export_job_failed", job_id=str(job.id), error=str(exc))
        return await _finish(job, job_repo, status=JobStatus.FAILED, error=str(exc))
