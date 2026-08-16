"""Generar el menú: lanzar el job y esperarlo.

Lo único que sobrevivió de la consola del entrenador. La generación tarda
segundos, así que va como job de fondo y la pantalla hace polling: es la razón
de que esto sean dos endpoints y no uno.
"""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.analytics import Event
from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.food_pool import resolve_allowed_foods
from nutriplan.application.jobs import (
    JOB_STALE_AFTER,
    generation_key,
    is_stale,
    new_job,
    run_generation_job,
)
from nutriplan.application.plan_cache import plan_cache_key
from nutriplan.application.recent_dishes import dishes_of_previous_week
from nutriplan.application.taste_profile import taste_profile_for
from nutriplan.container import Container
from nutriplan.domain.models import Client
from nutriplan.domain.restaurant import is_eating_out
from nutriplan.domain.taste import TasteProfile
from nutriplan.domain.week import iso_week_start
from nutriplan.ports.job_repository import Job, JobStatus
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    repos_of,
    tenant_of,
    track_event,
)
from nutriplan.ui.web.gate import week_gate
from nutriplan.ui.web.public_errors import GEN_FAILED, JOB_STALE

router = APIRouter()

# Lo que se le dice a alguien mientras espera. No son frases de relleno que
# rotan: son las etapas por las que pasa el motor, en su orden, y avanzan con el
# contador del polling. Veinte segundos con un mensaje fijo parecen un cuelgue.
LOADING_STAGES = (
    "Calculando tus calorías y tus macros…",
    "Eligiendo los alimentos de tu semana…",
    "Repartiendo las porciones al gramo…",
    "Escribiendo tus siete días…",
    "Armando tu lista de compra…",
)
# Cada poll son ~900 ms, así que una etapa dura unos cuatro segundos.
_POLLS_POR_ETAPA = 4


def _loading_msg(n: int) -> str:
    return LOADING_STAGES[min(n // _POLLS_POR_ETAPA, len(LOADING_STAGES) - 1)]


async def _profile(request: Request, session: AsyncSession) -> Client | None:
    return await repos_of(request, session).clients.get_by_user(account_id_of(request))


class _JobBeacon:
    """El estado del job en su propia transacción, corta y confirmada al vuelo.

    Escribirlo en la misma sesión que la generación tenía dos problemas: nadie
    fuera veía el `running` hasta el final (el polling acertaba de casualidad),
    y en SQLite esa transacción retenía el lock de escritura los veinte segundos
    que dura generar, así que cualquier otra petición moría con `database is
    locked`."""

    def __init__(self, container: Container, tenant_id: UUID) -> None:
        self._container = container
        self._tenant_id = tenant_id

    async def get(self, job_id: UUID) -> Job | None:
        async with self._container.session_factory() as session:
            return await self._container.repos(session, self._tenant_id).jobs.get(job_id)

    async def update(self, job: Job) -> None:
        async with self._container.session_factory() as session:
            await self._container.repos(session, self._tenant_id).jobs.update(job)
            await session.commit()

    async def touch(self, job_id: UUID) -> None:
        async with self._container.session_factory() as session:
            await self._container.repos(session, self._tenant_id).jobs.touch(job_id)
            await session.commit()


async def _run_generation(
    container: Container,
    job_id: UUID,
    client_id: UUID,
    variant: int,
    tenant_id: UUID,
    week_start: date,
    taste: TasteProfile | None,
) -> None:
    """Tarea de fondo: sesión propia para el plan, beacon aparte para el estado."""
    beacon = _JobBeacon(container, tenant_id)
    async with container.session_factory() as session:
        repos = container.repos(session, tenant_id)
        job = await beacon.get(job_id)
        if job is None:  # pragma: no cover — el job se creó en el request
            return
        s = container.settings
        select = s.llm_select_foods
        model = s.llm_model_generate if container.llm_client is not None and select else "engine-v1"
        await run_generation_job(
            job=job,
            job_repo=beacon,
            client_id=client_id,
            client_repo=repos.clients,
            targets_repo=repos.targets,
            food_repo=repos.foods,
            plan_repo=repos.plans,
            config=container.config_provider.get_nutrition_config(),
            llm=container.llm_client,
            offline_engine=container.offline_engine,
            prompts_dir=s.prompts_dir,
            model=model,
            variant=variant,
            catalog=container.meal_catalog,
            recipe_repo=container.dish_recipe_repo(session),
            commit=session.commit,
            rollback=session.rollback,
            select_foods=select,
            refine_names=s.llm_refine_names,
            recipe_model=s.llm_model_generate,
            refine_model=s.llm_model_generate,
            eager_recipes=s.llm_eager_recipes,
            select_max_retries=s.llm_select_max_retries if select else None,
            curated_recipes=list(container.recipe_catalog.recipes),
            week_start=week_start,
            taste=taste,
        )


def _spawn(request: Request, job_id: UUID, coro: Coroutine[Any, Any, None]) -> None:
    """La referencia fuerte evita que el GC recoja la tarea a mitad de vuelo."""
    task = asyncio.create_task(coro)
    in_flight: set[asyncio.Task[None]] = request.app.state.jobs_in_flight
    live: set[UUID] = request.app.state.generation_job_ids
    in_flight.add(task)
    live.add(job_id)

    def _done(t: asyncio.Task[None]) -> None:
        in_flight.discard(t)
        live.discard(job_id)

    task.add_done_callback(_done)


def _loading(request: Request, client: Client, job_id: str, n: int) -> HTMLResponse:
    return render(
        request,
        "partials/gen_loading.html",
        client=client,
        job_id=job_id,
        msg_index=n,
        loading_msg=_loading_msg(n),
        etapas=LOADING_STAGES,
        etapa=min(n // _POLLS_POR_ETAPA, len(LOADING_STAGES) - 1),
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
    from nutriplan.application.auto_week import promote_current_week

    client = await promote_current_week(client=client, plans=repos.plans, clients=repos.clients)

    # La puerta se decide aquí, no escondiendo el botón: la plantilla es una
    # sugerencia y esto cuesta llamadas a un proveedor de pago.
    gate = await week_gate(request, session, client)
    if not gate.allowed:
        return render(
            request,
            "partials/gen_error.html",
            client=client,
            error=gate.reason or "Todavía no puedes generar otra semana.",
            closure=gate.closure,
            membership=gate.membership,
        )

    targets_prev = await repos.targets.latest_for_client(client.id)
    targets = await compute_and_store_targets(
        client=client,
        config_provider=container.config_provider,
        targets_repo=repos.targets,
        formula=targets_prev.formula if targets_prev else None,
        overrides=(targets_prev.overrides or None) if targets_prev else None,
    )
    config = container.nutrition_config(client)
    allowed = await resolve_allowed_foods(client, food_repo=repos.foods, client_repo=repos.clients)
    if not allowed:
        return render(
            request,
            "partials/gen_error.html",
            client=client,
            error=(
                "No quedó ningún alimento disponible: revisa tus restricciones "
                "y lo que marcaste que no quieres ver."
            ),
        )

    # Cada «Generar otra semana» debe ser otra semilla. Contar filas no sirve:
    # al regenerar se borra el borrador anterior y siempre queda 1 → variant
    # congelado → mismo input_hash → mismo job DONE → la semana se «repite».
    previous = await repos.plans.list_for_client(client.id)
    variant = max((p.variant for p in previous), default=-1) + 1
    week = iso_week_start()
    taste = await taste_profile_for(client=client, ratings=repos.ratings, signals=repos.taste)
    # La despensa entra en la clave del job por lo mismo que entra en la del plan:
    # marcar la nevera y volver a generar es OTRA generación, no la de antes.
    on_hand = frozenset(await repos.clients.list_pantry_food_ids(client.id, week))
    recent_keys, _templates = dishes_of_previous_week(previous, week)
    input_hash = plan_cache_key(
        client=client,
        targets=targets,
        config=config,
        allowed=allowed,
        variant=variant,
        catalog=container.meal_catalog,
        week_start=week,
        taste=taste,
        llm=container.llm_client,
        select_foods=container.settings.llm_select_foods,
        refine_names=container.settings.llm_refine_names,
        on_hand=on_hand,
        recent_keys=recent_keys,
    )
    # La clave de idempotencia evita que dos toques al botón generen dos menús.
    # Lleva la semana dentro porque también es el registro de los intentos: el
    # tope de regeneraciones se cuenta sobre estas filas.
    key = generation_key(client_id=client.id, week_start=week, input_hash=input_hash)
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
    # Tomar el job es un UPDATE condicional: si otra petición (u otra instancia)
    # ya se lo llevó, aquí solo nos enganchamos a su resultado.
    claimed = await repos.jobs.claim(job.id, stale_before=datetime.now(UTC) - JOB_STALE_AFTER)
    await session.commit()
    if claimed:
        _spawn(
            request,
            job.id,
            _run_generation(container, job.id, client.id, variant, tenant_id, week, taste),
        )
        await track_event(
            request,
            session,
            Event.MENU_GENERATED,
            version=variant,
            alimentos_disponibles=len(allowed),
            con_ia=container.llm_client is not None,
        )
    return _loading(request, client, str(job.id), 0)


@router.get("/menu/receta", response_model=None)
async def meal_recipe(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    dia: int = 0,
    slot: str = "",
) -> HTMLResponse:
    """Una sola receta, al abrir el plato (flujo lean: no batch al generar)."""
    client = await _profile(request, session)
    if client is None or client.active_plan_id is None:
        return render(request, "partials/meal_prep.html", steps=[])

    repos = repos_of(request, session)
    plan = await repos.plans.get(client.active_plan_id)
    if plan is None:
        return render(request, "partials/meal_prep.html", steps=[])

    day_i = min(max(dia, 0), 6)
    day = next((d for d in plan.days if d.day_index == day_i), None)
    meal = next((m for m in (day.meals if day else []) if m.slot.value == slot), None)
    if meal is None or meal.is_free_meal or is_eating_out(meal) or not meal.dish_key:
        return render(request, "partials/meal_prep.html", steps=[])

    container = container_of(request)
    recipe_repo = container.dish_recipe_repo(session)
    cached = await recipe_repo.get_many([meal.dish_key])
    recipe = cached.get(meal.dish_key)

    # Al abrir el plato: si solo hay plantilla yaml, pedimos IA (prefer_ai).
    if recipe is None or (container.llm_client is not None and recipe.source == "yaml"):
        ids = [i.food_id for i in meal.items if i.food_id]
        foods = {f.id: f for f in await repos.foods.get_by_ids(ids)}
        # Suelta el lock ANTES de hablar con el modelo: dos HTMX en paralelo
        # más un /calificar no pueden pelearse el mismo writer de SQLite.
        await session.commit()
        from nutriplan.application.dish_recipes import recipes_for_week
        from nutriplan.domain.meal_template import static_recipes

        generated = await recipes_for_week(
            meals=[meal],
            catalog=foods,
            repo=recipe_repo,
            llm=container.llm_client,
            prompts_dir=container.settings.prompts_dir,
            model=container.settings.llm_model_generate,
            static=static_recipes(container.meal_catalog),
            curated=list(container.recipe_catalog.recipes),
            prefer_ai=True,
        )
        recipe = generated.get(meal.dish_key)
        await session.commit()

    if recipe is None or not recipe.steps:
        return render(request, "partials/meal_prep.html", steps=[])

    culinary = recipe.name_es if recipe.source in ("ai", "curated") and recipe.name_es else None
    return render(
        request,
        "partials/meal_prep.html",
        steps=list(recipe.steps),
        prep_minutes=recipe.prep_minutes,
        difficulty=recipe.difficulty,
        tips=recipe.tips,
        culinary_title=culinary,
    )


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

    if job_row is None or is_stale(job_row):
        # El proceso que lo generaba ya no está, o el id no existe. Mejor
        # decirlo que dejar girar la rueda: el botón vuelve a encolarlo.
        return render(request, "partials/gen_error.html", client=client, error=JOB_STALE)

    if job_row.status == JobStatus.FAILED:
        return render(request, "partials/gen_error.html", client=client, error=GEN_FAILED)

    if job_row.status == JobStatus.DONE:
        # Navegación completa: un hx-swap del <body> con el HTML entero dejaba
        # el shell roto y se veía otra vez «Todavía no tienes menú».
        #
        # Se aterriza en la COMPRA y no en el menú: lo primero que hay que hacer
        # con una semana recién hecha es comprarla. El plan ya está activo cuando
        # el job termina, así que la lista sale llena al instante.
        response = HTMLResponse("")
        response.headers["HX-Redirect"] = "/compra?nueva=1"
        return response

    return _loading(request, client, job, n + 1)
