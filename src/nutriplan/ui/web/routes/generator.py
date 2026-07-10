"""Generador (pantalla 1a): controles a la izquierda, vista previa a la derecha."""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.generate_plan import compute_input_hash
from nutriplan.application.jobs import new_job, run_generation_job
from nutriplan.container import Container
from nutriplan.domain.calculation import compute_targets as compute_targets_domain
from nutriplan.domain.food_filter import allowed_foods, forbidden_tags
from nutriplan.domain.models import Client, Goal, MacroFormula, NutritionTargets
from nutriplan.ports.job_repository import JobKind, JobStatus
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import container_of, db_session, render, repos_of

logger = structlog.get_logger(__name__)

router = APIRouter()

OVERRIDABLE = ("kcal", "protein_g", "carb_g", "fat_g")


async def _fresh_targets(request: Request, session: AsyncSession, client: Client,
                         formula: MacroFormula | None = None,
                         overrides: dict[str, float] | None = None) -> NutritionTargets:
    """Targets vigentes: los últimos persistidos o recién calculados.

    Al recomputar sin fórmula explícita (cambio de objetivo, nudge de un tile)
    se conserva la fórmula g/kg vigente — el modelo del entrenador no se pierde.
    """
    container = container_of(request)
    repos = repos_of(request, session)
    existing = await repos.targets.latest_for_client(client.id)
    if formula is None and overrides is None and existing is not None:
        return existing
    if formula is None and existing is not None:
        formula = existing.formula
    return await compute_and_store_targets(
        client=client, config_provider=container.config_provider,
        targets_repo=repos.targets, formula=formula, overrides=overrides,
    )


async def _generator_context(request: Request, session: AsyncSession,
                             client: Client, dia: int, editar: bool,
                             job_id: str | None = None) -> dict[str, Any]:
    container = container_of(request)
    repos = repos_of(request, session)
    config = container.config_provider.get_nutrition_config()
    targets = await _fresh_targets(request, session, client)

    # chips de alimentos: universo sin los restringidos, marcando los que le gustan
    universe = await repos.foods.list_universe()
    tags, _ = forbidden_tags(client.restrictions)
    visible = [f for f in universe if not (set(f.tags) & tags)]
    liked = set(map(str, client.liked_food_ids))
    groups = []
    for meta in presenter.FOOD_GROUPS:
        items = [
            {"id": str(f.id), "name": f.name_es.capitalize(), "on": str(f.id) in liked}
            for f in sorted(visible, key=lambda f: f.name_es) if f.category == meta["cat"]
        ]
        if items:
            groups.append({**meta, "items": items})

    # plan semanal más reciente + frescura frente a los insumos actuales
    cycles = await repos.plans.list_for_client(client.id)
    plan = presenter.latest_plan(cycles)
    stale = False
    day_view = None
    if plan is not None:
        allowed = allowed_foods(await repos.foods.get_by_ids(client.liked_food_ids),
                                client.restrictions)
        prompt = load_prompt(container.settings.prompts_dir, "plan_generation")
        current = compute_input_hash(client, targets, config.version, prompt.version, allowed)
        stale = plan.input_hash != current

        dia = max(0, min(dia, 6))
        food_ids = {p.food_id for d in plan.days for m in d.meals for p in m.portions}
        foods_by_id = {f.id: f for f in await repos.foods.get_by_ids(sorted(food_ids, key=str))}
        day = next(d for d in plan.days if d.day_index == dia)
        day_view = presenter.day_view(day, targets, config, foods_by_id)

    goal_meta = presenter.GOAL_META[client.goal]
    return {
        "client": client,
        "clients": await repos.clients.list(),
        "client_avatar": presenter.avatar_colors(client.id),
        "client_meta": (f"{presenter.SEX_LABELS[client.sex.value]} · "
                        f"{client.age_years} años · {presenter.fmt_g(client.weight_kg)} kg"),
        "goal_meta": goal_meta,
        "targets": targets,
        "tiles": presenter.macro_tiles(targets.daily),
        "formula": presenter.formula_view(client, targets),
        "has_overrides": bool(targets.overrides),
        "groups": groups,
        "plan": plan,
        "history_count": len(cycles),
        "stale": stale,
        "dia": max(0, min(dia, 6)),
        "dv": day_view,
        "editar": editar and plan is not None and plan.status.value == "draft",
        "job_id": job_id,
        "loading_msg": None,
    }


@router.get("/generador", response_class=HTMLResponse)
async def generator_page(request: Request,
                         session: Annotated[AsyncSession, Depends(db_session)],
                         cliente: str = "", dia: int = 0,
                         editar: int = 0) -> HTMLResponse:
    repos = repos_of(request, session)
    clients = await repos.clients.list()
    if not clients:
        return render(request, "generator.html", active_tab="generador", ctx=None)
    client = None
    if cliente:
        client = await repos.clients.get(UUID(cliente))
    client = client or clients[0]
    ctx = await _generator_context(request, session, client, dia, bool(editar))
    template = ("partials/generator_body.html"
                if request.headers.get("HX-Request") else "generator.html")
    return render(request, template, active_tab="generador", ctx=ctx)


async def _rerender(request: Request, session: AsyncSession, client: Client,
                    dia: int = 0) -> HTMLResponse:
    ctx = await _generator_context(request, session, client, dia, editar=False)
    return render(request, "partials/generator_body.html", active_tab="generador", ctx=ctx)


async def _get_client(request: Request, session: AsyncSession, cid: str) -> Client:
    client = await repos_of(request, session).clients.get(UUID(cid))
    if client is None:
        raise ValueError(f"Cliente {cid} no existe")
    return client


@router.post("/generador/{cid}/objetivo", response_class=HTMLResponse)
async def set_goal(request: Request, session: Annotated[AsyncSession, Depends(db_session)],
                   cid: str, goal: Annotated[str, Form()]) -> HTMLResponse:
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    client = client.model_copy(update={"goal": Goal(goal)})
    await repos.clients.update(client)
    await _fresh_targets(request, session, client, overrides={})  # recalcular sin overrides
    return await _rerender(request, session, client)


@router.post("/generador/{cid}/formula", response_class=HTMLResponse)
async def set_formula(request: Request, session: Annotated[AsyncSession, Depends(db_session)],
                      cid: str,
                      protein_g_per_kg: Annotated[str, Form()] = "",
                      fat_g_per_kg: Annotated[str, Form()] = "",
                      kcal_override: Annotated[str, Form()] = "") -> HTMLResponse:
    """Fórmula g/kg: el lever principal. Recalcula todo y descarta nudges viejos."""
    client = await _get_client(request, session, cid)

    def num(v: str) -> float | None:
        v = v.strip()
        if not v:
            return None
        try:
            return max(float(v), 0.0) or None
        except ValueError:
            return None

    formula = MacroFormula(
        protein_g_per_kg=num(protein_g_per_kg),
        fat_g_per_kg=num(fat_g_per_kg),
        kcal_override=num(kcal_override),
    )
    await _fresh_targets(request, session, client, formula=formula)
    return await _rerender(request, session, client)


@router.post("/generador/{cid}/macros", response_class=HTMLResponse)
async def set_macros(request: Request, session: Annotated[AsyncSession, Depends(db_session)],
                     cid: str,
                     kcal: Annotated[float, Form()],
                     protein_g: Annotated[float, Form()],
                     carb_g: Annotated[float, Form()],
                     fat_g: Annotated[float, Form()]) -> HTMLResponse:
    """El entrenador edita un tile: solo lo que difiere de la fórmula es override."""
    container = container_of(request)
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    config = container.config_provider.get_nutrition_config()
    existing = await repos.targets.latest_for_client(client.id)
    formula = existing.formula if existing else None
    base = compute_targets_domain(client, config, formula=formula).daily.model_dump()
    submitted = {"kcal": kcal, "protein_g": protein_g, "carb_g": carb_g, "fat_g": fat_g}
    overrides = {k: v for k, v in submitted.items() if abs(v - base[k]) > 0.5}
    await _fresh_targets(request, session, client, overrides=overrides)
    return await _rerender(request, session, client)


@router.post("/generador/{cid}/alimento", response_class=HTMLResponse)
async def toggle_food(request: Request, session: Annotated[AsyncSession, Depends(db_session)],
                      cid: str, food_id: Annotated[str, Form()]) -> HTMLResponse:
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    fid = UUID(food_id)
    liked = set(client.liked_food_ids)
    liked.symmetric_difference_update({fid})
    client = client.model_copy(update={"liked_food_ids": sorted(liked, key=str)})
    await repos.clients.update(client)
    return await _rerender(request, session, client)


@router.post("/generador/{cid}/restriccion", response_class=HTMLResponse)
async def toggle_restriction(request: Request,
                             session: Annotated[AsyncSession, Depends(db_session)],
                             cid: str, key: Annotated[str, Form()]) -> HTMLResponse:
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    restrictions = set(client.restrictions)
    restrictions.symmetric_difference_update({key})
    client = client.model_copy(update={"restrictions": sorted(restrictions)})
    await repos.clients.update(client)
    return await _rerender(request, session, client)


async def _run_generation(
    container: Container, job_id: UUID, client_id: UUID, variant: int
) -> None:
    """Tarea de fondo: sesión propia, job persistido, commit al final."""
    async with container.session_factory() as session:
        repos = container.repos(session)
        job = await repos.jobs.get(job_id)
        if job is None:  # pragma: no cover — el job se creó en el request
            return
        model = (container.settings.llm_model_generate
                 if container.llm_client is not None else "heuristic-v1")
        await run_generation_job(
            job=job, job_repo=repos.jobs, client_id=client_id,
            client_repo=repos.clients, targets_repo=repos.targets,
            food_repo=repos.foods, plan_repo=repos.plans,
            config=container.config_provider.get_nutrition_config(),
            llm=container.llm_client,
            prompts_dir=container.settings.prompts_dir, model=model, variant=variant,
        )
        await session.commit()


def _spawn(request: Request, coro: Coroutine[Any, Any, None]) -> None:
    """La referencia fuerte evita que el GC recoja la tarea a mitad de vuelo."""
    task = asyncio.create_task(coro)
    in_flight: set[asyncio.Task[None]] = request.app.state.jobs_in_flight
    in_flight.add(task)
    task.add_done_callback(in_flight.discard)


async def _launch_generation(request: Request, session: AsyncSession,
                             client: Client, variant: int) -> HTMLResponse:
    container = container_of(request)
    repos = repos_of(request, session)
    targets = await _fresh_targets(request, session, client)
    config = container.config_provider.get_nutrition_config()

    allowed = allowed_foods(await repos.foods.get_by_ids(client.liked_food_ids),
                            client.restrictions)
    if not allowed:
        return render(request, "partials/gen_error.html", client=client,
                      error="El conjunto permitido quedó vacío: marca alimentos que le "
                            "gusten (que no choquen con las restricciones).")

    prompt = load_prompt(container.settings.prompts_dir, "plan_generation")
    input_hash = compute_input_hash(
        client, targets, config.version, prompt.version, allowed, variant
    )
    key = f"gen:{client.id}:{input_hash[:16]}"

    job = await repos.jobs.get_by_idempotency_key(key)
    if job is None:
        job = new_job(tenant_id=container.tenant_id, kind=JobKind.GENERATE,
                      idempotency_key=key)
        await repos.jobs.add(job)
        await session.commit()
        _spawn(request, _run_generation(container, job.id, client.id, variant))
    elif job.status == JobStatus.FAILED:
        job = job.model_copy(update={"status": JobStatus.QUEUED, "error": None,
                                     "updated_at": datetime.now(UTC)})
        await repos.jobs.update(job)
        await session.commit()
        _spawn(request, _run_generation(container, job.id, client.id, variant))

    return render(request, "partials/gen_loading.html", client=client,
                  job_id=str(job.id), msg_index=0,
                  loading_msg=presenter.LOADING_MSGS[0].format(name=client.name.split()[0]))


@router.post("/generador/{cid}/generar", response_class=HTMLResponse)
async def start_generation(request: Request,
                           session: Annotated[AsyncSession, Depends(db_session)],
                           cid: str) -> HTMLResponse:
    client = await _get_client(request, session, cid)
    return await _launch_generation(request, session, client, variant=0)


@router.post("/generador/{cid}/nueva-version", response_class=HTMLResponse)
async def new_version(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)],
                      cid: str) -> HTMLResponse:
    """Otra versión del plan (mes siguiente): menú distinto al historial."""
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    variant = len(await repos.plans.list_for_client(client.id))
    return await _launch_generation(request, session, client, variant=variant)


@router.get("/generador/{cid}/estado", response_class=HTMLResponse)
async def generation_status(request: Request,
                            session: Annotated[AsyncSession, Depends(db_session)],
                            cid: str, job: str, n: int = 0) -> HTMLResponse:
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    job_row = await repos.jobs.get(UUID(job))

    if job_row is None or job_row.status == JobStatus.FAILED:
        error = (job_row.error if job_row else None) or "El job de generación no existe."
        return render(request, "partials/gen_error.html", client=client, error=error)

    if job_row.status == JobStatus.DONE:
        ctx = await _generator_context(request, session, client, dia=0, editar=False)
        ctx["celebrate"] = True
        return render(request, "partials/preview.html", ctx=ctx)

    name = client.name.split()[0]
    msgs = presenter.LOADING_MSGS
    return render(request, "partials/gen_loading.html", client=client, job_id=job,
                  msg_index=n + 1,
                  loading_msg=msgs[(n + 1) % len(msgs)].format(name=name))
