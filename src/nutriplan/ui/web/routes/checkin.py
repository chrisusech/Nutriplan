"""Cierre de semana: peso, calificaciones y cómo le fue.

Las tres cosas que hacen falta para armar la semana siguiente. El peso ajusta
las kcal, las notas dicen qué repetir, y el comentario es lo que la IA lee para
entender lo que los números no cuentan.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.analytics import Event
from nutriplan.application.food_pool import resolve_allowed_foods
from nutriplan.application.taste_profile import extract_taste_signals
from nutriplan.application.weekly_checkin import (
    submit_weekly_checkin,
    week_closure,
)
from nutriplan.domain.errors import ValidationError
from nutriplan.domain.week import iso_week_start
from nutriplan.domain.week_close import is_valid_comment
from nutriplan.domain.week_recap import week_recap
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    repos_of,
    track_event,
)

router = APIRouter()


@router.get("/check-in", response_model=None)
async def checkin_form(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)
    entry = await repos.weights.for_week(client.id, iso_week_start())
    plan = (
        await repos.plans.get(client.active_plan_id) if client.active_plan_id is not None else None
    )
    closure = await week_closure(
        client=client,
        weights=repos.weights,
        ratings=repos.ratings,
        meals_in_plan=sum(len(d.meals) for d in plan.days) if plan else 0,
    )
    recap = None
    if plan is not None:
        targets = await repos.targets.latest_for_client(client.id)
        if targets is not None:
            recap = week_recap(plan, targets.daily)
    return render(
        request,
        "checkin.html",
        active_tab="semana",
        perfil=client,
        last_weight=entry.weight_kg if entry else client.weight_kg,
        last_comment=entry.client_comment if entry else "",
        already=entry is not None,
        closure=closure,
        recap=recap,
    )


@router.post("/check-in", response_model=None)
async def checkin_submit(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    weight_kg: Annotated[float, Form()],
    comentario: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    container = container_of(request)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)

    plan = (
        await repos.plans.get(client.active_plan_id) if client.active_plan_id is not None else None
    )
    # La primera semana no tiene plan que comentar: solo se pesa.
    if plan is not None and not is_valid_comment(comentario):
        return await _form_with_error(
            request,
            session,
            weight_kg,
            comentario,
            "Cuéntanos en una frase cómo te fue esta semana: es lo que usamos "
            "para ajustar la siguiente.",
        )

    try:
        result = await submit_weekly_checkin(
            client=client,
            weight_kg=weight_kg,
            client_comment=comentario,
            client_repo=repos.clients,
            weights=repos.weights,
            targets_repo=repos.targets,
            config_provider=container.config_provider,
            llm=container.llm_client,
            prompts_dir=container.settings.prompts_dir,
            model=container.settings.llm_model_generate,
        )
    except ValidationError as exc:
        return await _form_with_error(request, session, weight_kg, comentario, str(exc))

    # Traducir lo que escribió a alimentos concretos. Soft-fail: sin IA la
    # semana se cierra igual, solo que con menos matiz.
    allowed = await resolve_allowed_foods(client, food_repo=repos.foods, client_repo=repos.clients)
    interpreted = await extract_taste_signals(
        client=client,
        plan_cycle_id=client.active_plan_id,
        week_start=iso_week_start(),
        weekly_comment=comentario,
        allowed=allowed,
        ratings=repos.ratings,
        store=repos.taste,
        llm=container.llm_client,
        prompts_dir=container.settings.prompts_dir,
        model=container.settings.llm_model_generate,
    )

    await track_event(
        request,
        session,
        Event.WEIGHT_CHECKIN,
        action=result.decision.action.value if result.decision else "first",
        delta_kcal=result.decision.delta_kcal if result.decision else 0,
        con_nota=bool(result.note),
        interpretado=interpreted,
    )
    await session.commit()
    return RedirectResponse("/listo", status_code=303)


@router.get("/listo", response_model=None)
async def checkin_ready(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    """La pantalla de 'semana cerrada'. Bookmarkable, para volver de la despensa."""
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)
    week = iso_week_start()
    entry = await repos.weights.for_week(client.id, week)
    if entry is None:
        return RedirectResponse("/check-in", status_code=303)
    targets = await repos.targets.latest_for_client(client.id)
    taste = await repos.taste.accumulated_for(client.id, weeks=1)
    interpreted = bool(taste.avoid_food_ids or taste.prefer_food_ids or taste.adjustments)
    return render(
        request,
        "checkin_done.html",
        active_tab="semana",
        perfil=client,
        decision=None,
        note=entry.note,
        targets=targets,
        interpreted=interpreted,
        en_casa_n=len(await repos.clients.list_pantry_food_ids(client.id, week)),
        pantry_volver="/listo",
    )


async def _form_with_error(
    request: Request,
    session: AsyncSession,
    weight_kg: float,
    comentario: str,
    error: str,
) -> HTMLResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    plan = (
        await repos.plans.get(client.active_plan_id)
        if client is not None and client.active_plan_id is not None
        else None
    )
    closure = (
        await week_closure(
            client=client,
            weights=repos.weights,
            ratings=repos.ratings,
            meals_in_plan=sum(len(d.meals) for d in plan.days) if plan else 0,
        )
        if client is not None
        else None
    )
    recap = None
    if client is not None and plan is not None:
        targets = await repos.targets.latest_for_client(client.id)
        if targets is not None:
            recap = week_recap(plan, targets.daily)
    return render(
        request,
        "checkin.html",
        active_tab="semana",
        perfil=client,
        last_weight=weight_kg,
        last_comment=comentario,
        closure=closure,
        recap=recap,
        error=error,
    )
