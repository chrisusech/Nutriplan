"""Generador (pantalla 1a): controles a la izquierda, vista previa a la derecha."""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.food_pool import resolve_allowed_foods
from nutriplan.application.generate_plan import compute_input_hash
from nutriplan.application.jobs import new_job, run_generation_job
from nutriplan.container import Container
from nutriplan.domain.calculation import apply_overrides, validate_daily
from nutriplan.domain.calculation import compute_targets as compute_targets_domain
from nutriplan.domain.errors import CalculationError
from nutriplan.domain.food_filter import forbidden_tags
from nutriplan.domain.models import (
    CORE_MEAL_SLOTS,
    Client,
    Goal,
    MacroFormula,
    MealSlot,
    NutritionTargets,
    Role,
)
from nutriplan.ports.job_repository import JobStatus
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import (
    acting_trainer,
    container_of,
    db_session,
    render,
    repos_of,
    tenant_of,
)

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
                             job_id: str | None = None,
                             formula_error: str | None = None,
                             ) -> dict[str, Any]:

    container = container_of(request)
    repos = repos_of(request, session)
    config = container.nutrition_config(client)
    targets = await _fresh_targets(request, session, client)

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

    cycles = await repos.plans.list_for_client(client.id)
    # EL plan del cliente es el que él marca como activo, no "el último que salió":
    # sin esto no se puede volver a la versión del mes pasado.
    plan = presenter.active_plan(client, cycles)
    # El peso de cada versión, para ver si el déficit está funcionando.
    history = presenter.weight_history(
        [(c, await repos.targets.get(c.targets_id)) for c in cycles]
    )
    stale = False
    day_view = None

    # El conjunto permitido se calcula SIEMPRE (antes solo si ya había plan): el
    # aviso de pobreza de pool tiene que verse ANTES de gastar una generación,
    # para que el entrenador arregle la lista en vez de recibir yogur siete días.
    allowed = await resolve_allowed_foods(
        client, food_repo=repos.foods, client_repo=repos.clients
    )
    catalog = container.meal_catalog
    pool_warnings = presenter.pool_warnings(allowed, catalog, targets.daily, config)

    if plan is not None:
        prompt = load_prompt(container.settings.prompts_dir, "plan_generation")
        current = compute_input_hash(
            client, targets, config.version, prompt.version, allowed,
            plan.variant,
            catalog_version=catalog.version,
        )
        stale = plan.input_hash != current

        dia = max(0, min(dia, 6))
        food_ids = {
            item.food_id for d in plan.days for m in d.meals
            for item in m.items if item.food_id
        }
        foods_by_id = {f.id: f for f in await repos.foods.get_by_ids(sorted(food_ids, key=str))}
        swap_pool = {f.id: f for f in allowed}
        for f in allowed:
            foods_by_id.setdefault(f.id, f)
        day = presenter.get_plan_day(plan, dia)
        if day is not None:
            day_view = presenter.day_view(
                day, targets, config, foods_by_id, swap_pool=list(swap_pool.values())
            )

    goal_meta = presenter.GOAL_META[client.goal]
    return {
        "client": client,
        "clients": await repos.clients.list_all(),
        "client_avatar": presenter.avatar_colors(client.id),
        "client_meta": (f"{presenter.SEX_LABELS[client.sex.value]} · "
                        f"{client.age_years} años · {presenter.fmt_g(client.weight_kg)} kg"),
        "goal_meta": goal_meta,
        "targets": targets,
        "tiles": presenter.macro_tiles(targets.daily),
        "formula": presenter.formula_view(client, targets, error=formula_error),
        "has_overrides": bool(targets.overrides),
        "groups": groups,
        "pool_warnings": pool_warnings,
        "plan": plan,
        "history_count": len(cycles),
        "weight_history": history,
        "stale": stale,
        "dia": max(0, min(dia, 6)),
        "dv": day_view,
        # Se puede editar el borrador y también corregir alimentos sobre la
        # definitiva (APPROVED); lo que no se puede es regenerar sin cupo.
        "editar": editar and plan is not None
        and plan.status.value in ("draft", "approved"),
        "job_id": job_id,
        "loading_msg": None,
    }


@router.get("/generador", response_class=HTMLResponse)
async def generator_page(request: Request,
                         session: Annotated[AsyncSession, Depends(db_session)],
                         cliente: str = "", dia: int = 0,
                         editar: int = 0) -> HTMLResponse:
    repos = repos_of(request, session)
    clients = await repos.clients.list_all()
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
                    dia: int = 0, formula_error: str | None = None) -> HTMLResponse:
    ctx = await _generator_context(request, session, client, dia, editar=False,
                                   formula_error=formula_error)
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
    try:
        # Sin overrides: cambiar de objetivo (y el botón de "restablecer") vuelve al
        # cálculo limpio.
        await _fresh_targets(request, session, client, overrides={})
    except CalculationError as exc:
        # Con una fórmula agresiva, el objetivo nuevo puede dejar el carbo bajo el piso.
        # Se explica en pantalla en vez de devolver un 500 que HTMX no pinta —y que deja
        # la pantalla congelada sin decir nada.
        return await _rerender(request, session, client, formula_error=str(exc))
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
    try:
        await _fresh_targets(request, session, client, formula=formula)
    except CalculationError as exc:
        # El carbo cierra el resto: subir proteína o grasa lo puede dejar bajo el
        # piso. Se muestra el porqué y se conservan los targets anteriores en vez
        # de tumbar la pantalla.
        return await _rerender(request, session, client, formula_error=str(exc))
    return await _rerender(request, session, client)


@router.post("/generador/{cid}/macros", response_class=HTMLResponse)
async def set_macros(request: Request, session: Annotated[AsyncSession, Depends(db_session)],
                     cid: str,
                     kcal: Annotated[float, Form()],
                     protein_g: Annotated[float, Form()],
                     carb_g: Annotated[float, Form()],
                     fat_g: Annotated[float, Form()]) -> HTMLResponse:
    """El entrenador edita un cuadrito. Cada macro es independiente; las kcal, el resultado.

    Lo que tocó de verdad se sabe comparando con lo que la pantalla MOSTRABA, no con la
    fórmula. Comparando con la fórmula, en la segunda edición las kcal —que ya se habían
    recalculado— parecían un ajuste manual que nadie había hecho: se congelaban, y el
    objetivo dejaba de cumplir kcal = 4·P + 4·C + 9·G. Un objetivo así no lo puede cuadrar
    ningún plato real, y el cliente se quedaba sin plan.
    """
    container = container_of(request)
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    config = container.nutrition_config(client)
    existing = await repos.targets.latest_for_client(client.id)
    formula = existing.formula if existing else None
    base = compute_targets_domain(client, config, formula=formula).daily
    current = existing.daily if existing else base

    submitted = {"kcal": kcal, "protein_g": protein_g, "carb_g": carb_g, "fat_g": fat_g}
    # Los cuadritos se pintan redondeados: se compara contra lo que se veía.
    shown = current.model_dump()
    touched = {k: v for k, v in submitted.items() if abs(v - round(shown[k])) > 0.5}
    if not touched:
        return await _rerender(request, session, client)
    # Si se tocó un macro, las kcal son su consecuencia: mandan los macros.
    if {"protein_g", "carb_g", "fat_g"} & set(touched):
        touched.pop("kcal", None)

    try:
        daily = apply_overrides(current, touched)
        validate_daily(daily, client, config)
        # Se guarda como ajuste todo lo que se aparte de la fórmula (incluido lo que el
        # invariante haya movido solo): es lo que enciende el badge de "Ajustado a mano".
        overrides = {
            field: getattr(daily, field)
            for field in OVERRIDABLE
            if abs(getattr(daily, field) - getattr(base, field)) > 0.5
        }
        await _fresh_targets(request, session, client, overrides=overrides)
    except CalculationError as exc:
        return await _rerender(request, session, client, formula_error=str(exc))
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


@router.post("/generador/{cid}/comidas", response_class=HTMLResponse)
async def toggle_meal(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)],
                      cid: str, slot: Annotated[str, Form()]) -> HTMLResponse:
    """Enciende o apaga un snack. Los macros se recalculan sobre las comidas que quedan."""
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    try:
        chosen = MealSlot(slot)
    except ValueError:
        return await _rerender(request, session, client)
    if chosen in CORE_MEAL_SLOTS:  # desayuno, almuerzo y cena no se quitan
        return await _rerender(request, session, client)

    slots = set(client.meal_slots)
    slots.symmetric_difference_update({chosen})
    client = client.model_copy(update={"meal_slots": [s for s in MealSlot if s in slots]})
    await repos.clients.update(client)
    # Los objetivos por comida cambian con el número de comidas: se recalculan ya.
    await _fresh_targets(request, session, client)
    return await _rerender(request, session, client)


@router.post("/generador/{cid}/peso", response_class=HTMLResponse)
async def set_weight(request: Request,
                     session: Annotated[AsyncSession, Depends(db_session)],
                     cid: str, peso: Annotated[str, Form()]) -> HTMLResponse:
    """Registra el peso de hoy y recalcula los macros con él.

    Es el seguimiento entero, y casi todo estaba ya escrito: `compute_and_store_targets`
    INSERTA una fila nueva en `nutrition_targets` (la tabla es append-only), así que
    los macros del mes pasado —y desde ahora, el peso con el que se calcularon— quedan
    intactos y consultables. Los cuadritos de macros siguen siendo editables a mano con
    el endpoint de siempre: quien no quiera la propuesta de la fórmula, la pisa.

    El peso es OPCIONAL. Si no lo tocas, se parte de los macros vigentes (los de la
    versión anterior) y se ajustan a mano. Las dos puertas llevan al mismo sitio.
    """
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    try:
        weight = float(str(peso).strip().replace(",", "."))
    except ValueError:
        return await _rerender(request, session, client)
    if not 25.0 <= weight <= 400.0:  # un dedo torcido no puede reventar la fórmula
        return await _rerender(request, session, client)

    client = client.model_copy(update={"weight_kg": weight})
    await repos.clients.update(client)

    existing = await repos.targets.latest_for_client(client.id)
    try:
        # Con `formula=` se fuerza el recálculo (si no, `_fresh_targets` devolvería los
        # targets viejos y el peso nuevo no movería nada).
        await _fresh_targets(
            request, session, client,
            formula=existing.formula if existing else MacroFormula(),
        )
    except CalculationError as exc:
        return await _rerender(request, session, client, formula_error=str(exc))
    return await _rerender(request, session, client)


@router.post("/generador/{cid}/comida-libre", response_class=HTMLResponse)
async def set_free_meal(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)],
                        cid: str,
                        dia: Annotated[str, Form()] = "",
                        slot: Annotated[str, Form()] = "") -> HTMLResponse:
    """La comida libre de la semana: un día y una comida, o ninguna.

    No recalcula los objetivos: la comida libre no cambia los macros del cliente,
    solo dice qué celda del plan no se pesa. Lo que sí cambia es el hash de entrada
    (`compute_input_hash`), así que el plan sale marcado como desactualizado y hay
    que regenerarlo — que es exactamente lo que debe pasar.
    """
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)

    day: int | None = None
    chosen: MealSlot | None = None
    if dia and slot:
        try:
            day, chosen = int(dia), MealSlot(slot)
        except ValueError:
            day, chosen = None, None
    # Vacío = quitar la comida libre. El validador del Client limpia el par a medias.
    client = client.model_copy(
        update={"free_meal_day": day, "free_meal_slot": chosen}
    )
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
    container: Container, job_id: UUID, client_id: UUID, variant: int, tenant_id: UUID,
) -> None:
    """Tarea de fondo: sesión propia, job persistido, commit al final."""
    async with container.session_factory() as session:
        repos = container.repos(session, tenant_id)
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


async def _launch_generation(request: Request, session: AsyncSession,
                             client: Client, variant: int,
                             ) -> HTMLResponse:
    container = container_of(request)
    repos = repos_of(request, session)

    # Cupo de versiones definitivas: el super_user no tiene tope. Para el entrenador,
    # al llegar al tope no puede generar un plan nuevo (solo corregir alimentos sobre
    # la definitiva). Los borradores no cuentan; solo las versiones aprobadas.
    actor = await acting_trainer(request, session)
    if actor is not None and actor.role == Role.USER and actor.max_menus is not None:
        approved = await repos.plans.count_approved_for_client(client.id)
        if approved >= actor.max_menus:
            return render(
                request, "partials/gen_error.html", client=client,
                error=(
                    f"Este cliente ya usó sus {actor.max_menus} versiones "
                    "definitivas. Puedes corregir alimentos puntuales del plan, pero no "
                    "generar uno nuevo. Pide al administrador ampliar el cupo."
                ),
            )

    targets = await _fresh_targets(request, session, client)
    config = container.nutrition_config(client)

    allowed = await resolve_allowed_foods(
        client, food_repo=repos.foods, client_repo=repos.clients
    )
    if not allowed:
        return render(request, "partials/gen_error.html", client=client,
                      error="El conjunto permitido quedó vacío: marca alimentos que le "
                            "gusten (que no choquen con las restricciones).")

    prompt = load_prompt(container.settings.prompts_dir, "plan_generation")
    input_hash = compute_input_hash(
        client, targets, config.version, prompt.version, allowed, variant,
        catalog_version=container.meal_catalog.version,
    )
    key = f"gen:{client.id}:{input_hash[:16]}"
    tenant_id = tenant_of(request)

    job = await repos.jobs.get_by_idempotency_key(key)
    if job is None:
        job = new_job(tenant_id=tenant_id, idempotency_key=key)
        try:
            await repos.jobs.add(job)
            await session.commit()
        except IntegrityError:
            await session.rollback()
            job = await repos.jobs.get_by_idempotency_key(key)
            if job is None:
                raise
        if job.status in (JobStatus.QUEUED, JobStatus.FAILED):
            _spawn(request, _run_generation(
                container, job.id, client.id, variant, tenant_id
            ))
    elif job.status == JobStatus.FAILED:
        job = job.model_copy(update={"status": JobStatus.QUEUED, "error": None,
                                     "updated_at": datetime.now(UTC)})
        await repos.jobs.update(job)
        await session.commit()
        _spawn(request, _run_generation(
            container, job.id, client.id, variant, tenant_id
        ))

    return render(request, "partials/gen_loading.html", client=client,
                  job_id=str(job.id), msg_index=0,
                  loading_msg=presenter.LOADING_MSGS[0].format(name=client.name.split()[0]))


@router.post("/generador/{cid}/generar", response_class=HTMLResponse)
async def start_generation(request: Request,
                           session: Annotated[AsyncSession, Depends(db_session)],
                           cid: str,
                           ) -> HTMLResponse:
    client = await _get_client(request, session, cid)
    return await _launch_generation(
        request, session, client, variant=0
    )


@router.post("/generador/{cid}/nueva-version", response_class=HTMLResponse)
async def new_version(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)],
                      cid: str,
                      ) -> HTMLResponse:
    """Otra versión del plan (mes siguiente): menú distinto al historial."""
    repos = repos_of(request, session)
    client = await _get_client(request, session, cid)
    variant = len(await repos.plans.list_for_client(client.id))
    return await _launch_generation(
        request, session, client, variant=variant
    )


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
