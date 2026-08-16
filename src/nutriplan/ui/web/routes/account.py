"""Perfil y opiniones: lo que la persona hace fuera de su menú."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.analytics import Event
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    is_super_user,
    landing_without_profile,
    render,
    repos_of,
    tenant_of,
    track_event,
)
from nutriplan.ui.web.gate import week_gate

router = APIRouter()

FEEDBACK_CATEGORIES = [
    ("receta", "Sobre un plato"),
    ("idea", "Una idea"),
    ("bug", "Algo no funciona"),
    ("general", "Otra cosa"),
]
MAX_MESSAGE = 4000


@router.get("/privacidad", response_class=HTMLResponse)
async def privacy(request: Request) -> HTMLResponse:
    """Pública: las tiendas exigen poder leerla sin cuenta."""
    return render(request, "privacidad.html", active_tab="")


@router.get("/terminos", response_class=HTMLResponse)
async def terms(request: Request) -> HTMLResponse:
    """Pública: App Store / Play la piden en el listing."""
    return render(request, "terminos.html", active_tab="")


@router.get("/soporte", response_class=HTMLResponse)
async def support(request: Request) -> HTMLResponse:
    """Pública: URL de soporte para las fichas de las tiendas."""
    return render(request, "soporte.html", active_tab="")


@router.get("/plan-inactivo", response_class=HTMLResponse)
async def inactive_plan(request: Request) -> HTMLResponse:
    """Lo único que ve quien tiene la cuenta desactivada.

    El guard manda aquí cualquier otra ruta; quien esté activo no llega nunca.
    """
    return render(request, "plan_inactivo.html", active_tab="inactivo")


@router.get("/consentimiento", response_class=HTMLResponse)
async def consent_page(request: Request) -> HTMLResponse:
    return render(request, "consentimiento.html", active_tab="")


@router.post("/consentimiento", response_model=None)
async def give_consent(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    acepta: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Sin esto no se registra un solo evento suyo en app_events."""
    if acepta != "1":
        return RedirectResponse("/consentimiento", status_code=303)
    await container_of(request).auth_repo(session).grant_analytics_consent(account_id_of(request))
    # El primer evento que se puede registrar es, precisamente, el permiso.
    await track_event(request, session, Event.CONSENT_GIVEN)
    return RedirectResponse(landing_without_profile(request), status_code=303)


@router.get("/perfil", response_class=HTMLResponse)
async def profile(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    gate = await week_gate(request, session, client) if client is not None else None
    targets = await repos.targets.latest_for_client(client.id) if client is not None else None
    if client is not None and targets is None:
        from nutriplan.domain.calculation import compute_targets

        targets = compute_targets(client, container_of(request).nutrition_config(client))
    from nutriplan.domain.adapt_targets import USER_LOCK

    locked = bool(targets and (targets.overrides or {}).get(USER_LOCK))
    racha = 0
    if client is not None:
        from nutriplan.domain.streak import current_streak

        racha = current_streak(await repos.plans.list_for_client(client.id))
    return render(
        request,
        "profile.html",
        active_tab="perfil",
        perfil=client,
        racha=racha,
        targets=targets,
        macros_locked=locked,
        aviso=request.query_params.get("aviso", ""),
        error=request.query_params.get("error", ""),
        can_regenerate=bool(gate and gate.allowed and is_super_user(request)),
        waiting_sunday=bool(gate and gate.reason.startswith("El domingo")),
        unlock_hint=gate.reason if gate else "",
        needs_checkin=gate.needs_checkin if gate else False,
        membership=gate.membership if gate else None,
        closure=gate.closure if gate else None,
    )


@router.post("/perfil/macros", response_model=None)
async def save_macros(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    kcal: Annotated[float, Form()],
    protein_g: Annotated[float, Form()],
    carb_g: Annotated[float, Form()],
    fat_g: Annotated[float, Form()],
    adaptar: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Los números de la persona, siempre editables. USER_LOCK salvo si pide adaptarse."""
    from urllib.parse import quote

    from nutriplan.application.compute_targets import compute_and_store_targets
    from nutriplan.application.user_macros import user_macro_plan
    from nutriplan.domain.errors import CalculationError

    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)
    container = container_of(request)
    try:
        formula, overrides, warning = user_macro_plan(
            client=client,
            config=container.nutrition_config(client),
            kcal=kcal,
            protein_g=protein_g,
            carb_g=carb_g,
            fat_g=fat_g,
            adapt_with_weight=adaptar == "1",
        )
        await compute_and_store_targets(
            client=client,
            config_provider=container.config_provider,
            targets_repo=repos.targets,
            formula=formula,
            overrides=overrides,
        )
    except CalculationError as exc:
        return RedirectResponse("/perfil?error=" + quote(str(exc)), status_code=303)
    qs = "?aviso=" + quote(warning) if warning else ""
    return RedirectResponse("/perfil" + qs, status_code=303)


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse:
    return render(
        request,
        "feedback.html",
        active_tab="feedback",
        categories=FEEDBACK_CATEGORIES,
        enviado=request.query_params.get("gracias") == "1",
    )


@router.post("/feedback", response_model=None)
async def submit_feedback(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    category: Annotated[str, Form()],
    message: Annotated[str, Form()],
    nps: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Lo que la gente tiene para decir. Es medio producto del BETA."""
    texto = message.strip()[:MAX_MESSAGE]
    if not texto:
        return render(
            request,
            "feedback.html",
            active_tab="feedback",
            categories=FEEDBACK_CATEGORIES,
            error="Escríbenos algo, aunque sea corto.",
        )
    valid = {key for key, _ in FEEDBACK_CATEGORIES}
    await repos_of(request, session).ratings.add_feedback(
        user_id=account_id_of(request),
        category=category if category in valid else "general",
        message=texto,
        nps=int(nps) if nps.isdigit() and 0 <= int(nps) <= 10 else None,
        platform=request.headers.get("X-Platform", "web")[:20],
    )
    await track_event(request, session, Event.FEEDBACK_SENT, category=category, tiene_nps=bool(nps))
    return RedirectResponse("/feedback?gracias=1", status_code=303)


@router.post("/device-tokens", response_model=None)
async def register_device_token(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    platform: Annotated[str, Form()],
    token: Annotated[str, Form()],
) -> PlainTextResponse:
    """Registra el token APNs/FCM que envía Capacitor PushNotifications."""
    try:
        await (
            container_of(request)
            .device_token_repo(session)
            .upsert(
                tenant_id=tenant_of(request),
                user_id=account_id_of(request),
                platform=platform,
                token=token,
            )
        )
    except ValueError:
        return PlainTextResponse("token inválido", status_code=400)
    return PlainTextResponse("ok", status_code=204)
