"""El domingo arma la semana siguiente a quien ya cerró.

No regenera la que se está comiendo. Si el menú del lunes nuevo ya existe,
solo se activa cuando ese lunes llega.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import structlog

from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.jobs import (
    JOB_STALE_AFTER,
    generation_key,
    new_job,
    run_generation_job,
)
from nutriplan.application.membership import membership_of
from nutriplan.application.plan_cache import plan_cache_key
from nutriplan.application.recent_dishes import dishes_of_previous_week
from nutriplan.application.taste_profile import taste_profile_for
from nutriplan.container import Container
from nutriplan.domain.auto_week import (
    closed_and_target_weeks,
    in_auto_window,
    now_bogota,
    should_activate,
)
from nutriplan.domain.models import Account, Client
from nutriplan.domain.week import iso_week_start
from nutriplan.ports.job_repository import JobStatus
from nutriplan.ports.repository import ClientRepository, PlanRepository

logger = structlog.get_logger(__name__)


async def promote_current_week(
    *,
    client: Client,
    plans: PlanRepository,
    clients: ClientRepository,
    today: date | None = None,
) -> Client:
    """Si ya hay menú de este lunes, ese es EL plan. El de ayer se archiva."""
    week = iso_week_start(today or now_bogota().date())
    current = await plans.for_week(client.id, week)
    if current is None or client.active_plan_id == current.id:
        return client
    await clients.set_active_plan(client.id, current.id)
    return client.model_copy(update={"active_plan_id": current.id})


async def run_auto_week(
    container: Container, *, when: datetime | None = None, force: bool = False
) -> int:
    """Genera el menú objetivo a quien cerró y tiene saldo. Devuelve cuántos."""
    if not force and not in_auto_window(when):
        return 0
    closed_week, target_week = closed_and_target_weeks(when)
    activate = should_activate(target_week, when)
    generated = 0
    async with container.session_factory() as session:
        admin = container.admin_repo(session)
        memberships = container.membership_repo(session)
        auth = container.auth_repo(session)
        candidates = await admin.closed_without_next_plan(
            closed_week=closed_week, target_week=target_week
        )
        for cand in candidates:
            account = await auth.get_by_id(cand.user_id)
            if account is None:
                continue
            repos = container.repos(session, cand.tenant_id)
            client = await repos.clients.get(cand.client_id)
            if client is None:
                continue
            state = await membership_of(
                account=account,
                client_id=client.id,
                memberships=memberships,
                plans=repos.plans,
            )
            if not state.can_generate:
                continue
            ok = await _generate_one(
                container,
                session=session,
                client=client,
                account=account,
                week=target_week,
                activate=activate,
            )
            if ok:
                generated += 1
        await session.commit()
    if generated:
        logger.info(
            "auto_week_done",
            n=generated,
            closed=str(closed_week),
            target=str(target_week),
        )
    return generated


async def _generate_one(
    container: Container,
    *,
    session: object,
    client: Client,
    account: Account,
    week: date,
    activate: bool,
) -> bool:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    repos = container.repos(session, client.tenant_id)
    targets_prev = await repos.targets.latest_for_client(client.id)
    targets = await compute_and_store_targets(
        client=client,
        config_provider=container.config_provider,
        targets_repo=repos.targets,
        formula=targets_prev.formula if targets_prev else None,
        overrides=(targets_prev.overrides or None) if targets_prev else None,
    )
    taste = await taste_profile_for(client=client, ratings=repos.ratings, signals=repos.taste)
    previous = await repos.plans.list_for_client(client.id)
    variant = max((p.variant for p in previous), default=-1) + 1
    config = container.nutrition_config(client)
    from nutriplan.application.food_pool import resolve_allowed_foods

    allowed = await resolve_allowed_foods(client, food_repo=repos.foods, client_repo=repos.clients)
    if not allowed:
        logger.warning("auto_week_skip_empty_pool", client_id=str(client.id))
        return False
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
    key = generation_key(client_id=client.id, week_start=week, input_hash=input_hash)
    job = await repos.jobs.get_by_idempotency_key(key)
    if job is None:
        job = new_job(tenant_id=client.tenant_id, idempotency_key=key)
        await repos.jobs.add(job)
    claimed = await repos.jobs.claim(job.id, stale_before=datetime.now(UTC) - JOB_STALE_AFTER)
    if not claimed:
        return False
    _ = account
    finished = await run_generation_job(
        job=job,
        job_repo=repos.jobs,
        client_id=client.id,
        client_repo=repos.clients,
        targets_repo=repos.targets,
        food_repo=repos.foods,
        plan_repo=repos.plans,
        config=config,
        llm=container.llm_client,
        offline_engine=container.offline_engine,
        prompts_dir=container.settings.prompts_dir,
        model=container.settings.llm_model_generate
        if container.settings.llm_select_foods
        else "engine-v1",
        variant=variant,
        catalog=container.meal_catalog,
        recipe_repo=container.dish_recipe_repo(session),
        commit=session.commit,
        rollback=session.rollback,
        select_foods=container.settings.llm_select_foods,
        refine_names=container.settings.llm_refine_names,
        week_start=week,
        taste=taste,
        activate=activate,
    )
    if finished.status is not JobStatus.DONE:
        logger.warning("auto_week_failed", client_id=str(client.id), error=finished.error)
        return False
    return True


async def auto_week_loop(container: Container) -> None:
    """Cada cuarto de hora, si estamos en la ventana."""
    import asyncio

    while True:
        await asyncio.sleep(15 * 60)
        try:
            await run_auto_week(container)
        except Exception:
            logger.exception("auto_week_tick_failed")
