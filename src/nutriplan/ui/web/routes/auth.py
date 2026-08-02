"""Alta pública, login (correo, Google o Apple) y baja de cuenta."""

from typing import Annotated
from urllib.parse import quote

import structlog
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import TenantRow
from nutriplan.adapters.oauth import (
    OAuthError,
    verify_apple_id_token,
    verify_google_id_token,
)
from nutriplan.adapters.password_reset_token import verify_token
from nutriplan.application.account_lifecycle import (
    confirm_email,
    delete_account,
    deletion_receipt,
    send_verification_email,
)
from nutriplan.application.auth import (
    SignupError,
    authenticate,
    register,
    sign_in_with_provider,
)
from nutriplan.application.password_reset import complete_password_reset, request_password_reset
from nutriplan.domain.models import Account, AuthProvider
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    tenant_of,
)

router = APIRouter()
logger = structlog.get_logger(__name__)


def _set_session(request: Request, account: Account) -> None:
    # Se limpia antes de escribir: si alguien fijó una sesión previa, entrar no
    # puede heredarla (fijación de sesión). También renueva el token CSRF.
    request.session.clear()
    request.session["user_id"] = str(account.id)
    request.session["tenant_id"] = str(account.tenant_id)
    request.session["name"] = account.name
    request.session["email"] = account.email
    request.session["role"] = account.role.value


@router.get("/recuperar", response_model=None)
async def password_reset_page(request: Request) -> HTMLResponse | RedirectResponse:
    return render(request, "password_reset_request.html")


@router.post("/recuperar", response_model=None)
async def password_reset_request(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    email: Annotated[str, Form()],
) -> HTMLResponse | RedirectResponse:
    container = container_of(request)
    try:
        link = await request_password_reset(
            email=email,
            auth_repo=container.auth_repo(session),
            session_secret=container.settings.session_secret,
        )
    except SignupError as exc:
        return render(request, "password_reset_request.html", error=str(exc), email=email)
    # La respuesta es la MISMA exista o no la cuenta: distinguirlas convierte
    # este formulario en un detector de correos registrados.
    if link is not None:
        url = f"{container.settings.base_url.rstrip('/')}{link.url_path}"
        try:
            await container.mailer.send(
                to=link.email,
                subject="Recupera tu contraseña · NutriPlan",
                body=(
                    f"Para elegir una contraseña nueva entra aquí:\n{url}\n\n"
                    f"El enlace vence en {link.expires_minutes} minutos.\n"
                    "Si no fuiste tú, ignora este mensaje."
                ),
            )
        except Exception as exc:  # noqa: BLE001 - no revelar fallos de entrega
            logger.warning("password_reset_email_failed", error=str(exc))
    return render(request, "password_reset_request.html", sent=True, email=email)


@router.get("/recuperar/{token}", response_model=None)
async def password_reset_confirm_page(
    request: Request, token: str
) -> HTMLResponse | RedirectResponse:
    container = container_of(request)
    email = verify_token(token, secret=container.settings.session_secret)
    if email is None:
        return render(
            request,
            "password_reset_request.html",
            error="El enlace expiró o no es válido. Genera uno nuevo.",
        )
    return render(request, "password_reset_confirm.html", token=token, email=email)


@router.post("/recuperar/{token}", response_model=None)
async def password_reset_confirm(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    token: str,
    password: Annotated[str, Form()],
    password_confirm: Annotated[str, Form()],
) -> HTMLResponse | RedirectResponse:
    if password != password_confirm:
        email = verify_token(token, secret=container_of(request).settings.session_secret)
        return render(
            request,
            "password_reset_confirm.html",
            token=token,
            email=email or "",
            error="Las contraseñas no coinciden.",
        )
    container = container_of(request)
    try:
        email = await complete_password_reset(
            token=token,
            new_password=password,
            auth_repo=container.auth_repo(session),
            session_secret=container.settings.session_secret,
        )
        trainer_row = await container.auth_repo(session).get_by_email(email)
        if trainer_row is None:
            raise SignupError("La cuenta ya no existe.")
        trainer, _ = trainer_row
        if await session.get(TenantRow, trainer.tenant_id) is None:
            return render(
                request,
                "password_reset_request.html",
                error="La cuenta quedó desincronizada. Regístrate de nuevo.",
                email=email,
            )
        _set_session(request, trainer)
    except SignupError as exc:
        email = verify_token(token, secret=container.settings.session_secret)
        return render(
            request,
            "password_reset_confirm.html",
            token=token,
            email=email or "",
            error=str(exc),
        )
    return RedirectResponse("/", status_code=303)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    error = None
    if request.query_params.get("sesion") == "expirada":
        error = (
            "Tu sesión ya no es válida — suele pasar tras cambiar o reiniciar la base "
            "de datos. Vuelve a entrar o crea una cuenta nueva."
        )
    return render(request, "auth.html", mode="login", error=error)


@router.post("/login", response_model=None)
async def login(request: Request,
                session: Annotated[AsyncSession, Depends(db_session)],
                email: Annotated[str, Form()],
                password: Annotated[str, Form()]) -> HTMLResponse | RedirectResponse:
    auth_repo = container_of(request).auth_repo(session)
    trainer = await authenticate(email=email, password=password, auth_repo=auth_repo)
    # Un solo mensaje para todos los fallos: distinguirlos convierte el login en
    # un detector de correos registrados.
    generico = "Correo o contraseña incorrectos."
    if trainer is None:
        return render(request, "auth.html", mode="login", error=generico)
    if await session.get(TenantRow, trainer.tenant_id) is None:
        return render(
            request,
            "auth.html",
            mode="login",
            error=generico,
        )
    if not trainer.is_active:
        return render(
            request,
            "auth.html",
            mode="login",
            error=generico,
        )
    _set_session(request, trainer)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --- Alta pública -----------------------------------------------------------


@router.get("/registro", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    settings = container_of(request).settings
    return render(
        request, "auth.html", mode="signup",
        google_client_id=settings.google_client_id,
        apple_client_id=settings.apple_client_id,
    )


@router.post("/registro", response_model=None)
async def signup(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> HTMLResponse | RedirectResponse:
    container = container_of(request)
    try:
        account = await register(
            name=name, email=email, password=password,
            auth_repo=container.auth_repo(session),
            branding_dir=container.settings.branding_dir,
        )
    except SignupError as exc:
        return render(request, "auth.html", mode="signup", error=str(exc))

    # Que el correo no llegue no puede tumbar el alta: la cuenta ya existe y se
    # puede reenviar. Bloquear aquí regalaría cuentas a medias.
    try:
        await send_verification_email(
            email=account.email, name=account.name,
            base_url=container.settings.base_url,
            secret=container.settings.session_secret,
            mailer=container.mailer,
        )
    except Exception as exc:  # noqa: BLE001 - el correo es best-effort
        logger.warning("verification_email_failed", error=str(exc))

    _set_session(request, account)
    return RedirectResponse("/consentimiento", status_code=303)


@router.get("/verificar/{token}", response_model=None)
async def verify_email(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    token: str,
) -> HTMLResponse | RedirectResponse:
    container = container_of(request)
    try:
        await confirm_email(
            token=token, secret=container.settings.session_secret,
            auth_repo=container.auth_repo(session),
        )
    except SignupError as exc:
        return render(request, "auth.html", mode="login", error=str(exc))
    return RedirectResponse("/login?verificado=1", status_code=303)


# --- Google y Apple ---------------------------------------------------------


@router.post("/auth/oauth/{provider}", response_model=None)
async def oauth_sign_in(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    provider: str,
    id_token: Annotated[str, Form()],
) -> HTMLResponse | RedirectResponse:
    """Canjea el ID token del cliente nativo por una sesión.

    El token SIEMPRE se verifica contra el proveedor: sin eso, mandar un correo
    ajeno en el formulario sería suficiente para entrar como esa persona.
    """
    container = container_of(request)
    settings = container.settings
    try:
        if provider == AuthProvider.GOOGLE:
            identity = await verify_google_id_token(
                id_token, client_id=settings.google_client_id
            )
        elif provider == AuthProvider.APPLE:
            identity = await verify_apple_id_token(
                id_token, client_id=settings.apple_client_id
            )
        else:
            return render(request, "auth.html", mode="login",
                          error="Ese proveedor no está disponible.")
        account = await sign_in_with_provider(
            provider=identity.provider, subject=identity.subject,
            email=identity.email, name=identity.name,
            email_verified=identity.email_verified,
            auth_repo=container.auth_repo(session),
            branding_dir=settings.branding_dir,
        )
    except (OAuthError, SignupError) as exc:
        return render(request, "auth.html", mode="login", error=str(exc))

    _set_session(request, account)
    profile = await container.repos(session, tenant_id=account.tenant_id).clients.get_by_user(
        account.id
    )
    if profile:
        return RedirectResponse("/", status_code=303)
    destino = "/onboarding" if account.consent_analytics_at else "/consentimiento"
    return RedirectResponse(destino, status_code=303)


# --- Baja de cuenta ---------------------------------------------------------


@router.post("/perfil/eliminar", response_model=None)
async def delete_my_account(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> RedirectResponse:
    """Borra la cuenta y sus datos. Requisito de tienda (Apple 5.1.1(v))."""
    container = container_of(request)
    await delete_account(
        user_id=account_id_of(request),
        tenant_id=tenant_of(request),
        eraser=container.account_eraser(session),
    )
    request.session.clear()
    return RedirectResponse(f"/login?baja={quote(deletion_receipt())}", status_code=303)
