"""Generar el menú: lanzar el job y esperarlo.

Lo único que sobrevivió de la consola del entrenador. La generación tarda
segundos, así que va como job de fondo y la pantalla hace polling: es la razón
de que esto sean dos endpoints y no uno.
"""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.food_pool import resolve_allowed_foods
from nutriplan.application.generate_plan import PLAN_PROMPT_VERSION, compute_input_hash
from nutriplan.application.jobs import new_job, run_generation_job
from nutriplan.container import Container
from nutriplan.domain.models import Client
from nutriplan.ports.job_repository import JobStatus
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    repos_of,
    tenant_of,
)

router = APIRouter()

# Lo que se le dice a alguien mientras espera. Que cambien evita la sensación
# de que la pantalla se colgó.
LOADING_MSGS = [
    "Buscando platos que te cuadren…",
    "Ajustando las porciones al gramo…",
    "Repartiendo las comidas de la semana…",
    "Dándole una última mirada…",
]


async def _profile(request: Request, session: AsyncSession) -> Client | None:
    return await repos_of(request, session).clients.get_by_user(account_id_of(request))


async def _run_generation(
    container: Container, job_id: UUID, client_id: UUID, variant: int, tenant_id: UUID
) -> None:
    """Tarea de fondo: sesión propia, job persistido, commit al final."""
    async with container.session_factory() as session:
        repos = container.repos(session, tenant_id)
        job = await repos.jobs.get(job_id)
        if job is None:  # pragma: no cover — el job se creó en el request
            return
        model = (
            container.settings.llm_model_generate
            if container.llm_client is not None
            else "engine-v1"
        )
        await run_generation_job(
            job=job, job_repo=repos.jobs, client_id=client_id,
            client_repo=repos.clients, targets_repo=repos.targets,
            food_repo=repos.foods, plan_repo=repos.plans,
            config=container.config_provider.get_nutrition_config(),
            llm=container.llm_client,
            prompts_dir=container.settings.prompts_dir, model=model, variant=variant,
            catalog=container.meal_catalog,
            recipe_repo=container.dish_recipe_repo(session),
        )
        await session.commit()


def _spawn(request: Request, coro: Coroutine[Any, Any, None]) -> None:
    """La referencia fuerte evita que el GC recoja la tarea a mitad de vuelo."""
    task = asyncio.create_task(coro)
    in_flight: set[asyncio.Task[None]] = request.app.state.jobs_in_flight
    in_flight.add(task)
    task.add_done_callback(in_flight.discard)


def _loading(request: Request, client: Client, job_id: str, n: int) -> HTMLResponse:
    return render(
        request, "partials/gen_loading.html", client=client, job_id=job_id,
        msg_index=n, loading_msg=LOADING_MSGS[n % len(LOADING_MSGS)],
    )


@router.post("/menu/generar", response_model=None)
async def start_generation(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse:
    """Arranca la generación. Si ya hay una igual en curso, se engancha a ella."""
    client = await _profile(request, session)
    if client is None:
        return render(request, "partials/gen_error.html", error="Aún no tienes perfil.")

    container = container_of(request)
    repos = repos_of(request, session)
    targets = await compute_and_store_targets(
        client=client,
        config_provider=container.config_provider,
        targets_repo=repos.targets,
    )
    config = container.nutrition_config(client)
    allowed = await resolve_allowed_foods(
        client, food_repo=repos.foods, client_repo=repos.clients
    )
    if not allowed:
        return render(
            request, "partials/gen_error.html", client=client,
            error=(
                "No quedó ningún alimento disponible: revisa tus restricciones "
                "y lo que marcaste que no quieres ver."
            ),
        )

    # La versión: cada menú nuevo es una semana distinta de las anteriores.
    variant = len(await repos.plans.list_for_client(client.id))
    prompt = load_prompt(container.settings.prompts_dir, "plan_generation", PLAN_PROMPT_VERSION)
    input_hash = compute_input_hash(
        client, targets, config.version, prompt.version, allowed, variant,
        catalog_version=container.meal_catalog.version,
    )
    # La clave de idempotencia evita que dos toques al botón generen dos menús.
    key = f"gen:{client.id}:{input_hash[:16]}"
    tenant_id = tenant_of(request)

    job = await repos.jobs.get_by_idempotency_key(key)
    if job is None:
        job = new_job(tenant_id=tenant_id, idempotency_key=key)
        try:
            await repos.jobs.add(job)
            await session.commit()
        except IntegrityError:
            # Otra petición ganó la carrera: nos enganchamos a su job.
            await session.rollback()
            job = await repos.jobs.get_by_idempotency_key(key)
            if job is None:
                raise
        if job.status in (JobStatus.QUEUED, JobStatus.FAILED):
            _spawn(request, _run_generation(container, job.id, client.id, variant, tenant_id))
    elif job.status == JobStatus.FAILED:
        job = job.model_copy(
            update={"status": JobStatus.QUEUED, "error": None, "updated_at": datetime.now(UTC)}
        )
        await repos.jobs.update(job)
        await session.commit()
        _spawn(request, _run_generation(container, job.id, client.id, variant, tenant_id))

    return _loading(request, client, str(job.id), 0)


@router.get("/menu/estado", response_model=None)
async def generation_status(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    job: str,
    n: int = 0,
) -> HTMLResponse:
    """Polling: sigue esperando, falló, o ya está."""
    client = await _profile(request, session)
    if client is None:
        return render(request, "partials/gen_error.html", error="Aún no tienes perfil.")

    repos = repos_of(request, session)
    try:
        job_row = await repos.jobs.get(UUID(job))
    except ValueError:
        job_row = None

    if job_row is None or job_row.status == JobStatus.FAILED:
        error = (job_row.error if job_row else None) or "La generación no se encontró."
        return render(request, "partials/gen_error.html", client=client, error=error)

    if job_row.status == JobStatus.DONE:
        return render(request, "partials/gen_done.html")

    return _loading(request, client, job, n + 1)
