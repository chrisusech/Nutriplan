"""Alta y login de entrenadores (Workstream G). Sesión por cookie firmada."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import TenantRow
from nutriplan.adapters.password_reset_token import verify_token
from nutriplan.application.auth import SignupError, authenticate, signup_trainer
from nutriplan.application.password_reset import complete_password_reset, request_password_reset
from nutriplan.config.settings import Environment
from nutriplan.domain.models import Trainer
from nutriplan.ui.web.deps import container_of, db_session, render

router = APIRouter()


def _set_session(request: Request, trainer: Trainer) -> None:
    request.session["tenant_id"] = str(trainer.tenant_id)
    request.session["name"] = trainer.name
    request.session["email"] = trainer.email
    request.session["role"] = trainer.role
    request.session["client_id"] = str(trainer.client_id) if trainer.client_id else ""


def _home_for(trainer: Trainer) -> str:
    return "/portal" if trainer.role == "client" else "/"


def _local_only(request: Request) -> bool:
    return container_of(request).settings.env is Environment.LOCAL


@router.get("/recuperar", response_model=None)
async def password_reset_page(request: Request) -> HTMLResponse | RedirectResponse:
    if not _local_only(request):
        return RedirectResponse("/login", status_code=303)
    return render(request, "password_reset_request.html")


@router.post("/recuperar", response_model=None)
async def password_reset_request(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    email: Annotated[str, Form()],
) -> HTMLResponse | RedirectResponse:
    if not _local_only(request):
        return RedirectResponse("/login", status_code=303)
    container = container_of(request)
    try:
        link = await request_password_reset(
            email=email,
            auth_repo=container.auth_repo(session),
            session_secret=container.settings.session_secret,
        )
    except SignupError as exc:
        return render(request, "password_reset_request.html", error=str(exc), email=email)
    if link is None:
        return render(request, "password_reset_request.html", not_found=True, email=email)
    return render(request, "password_reset_request.html", reset_link=link, email=email)


@router.get("/recuperar/{token}", response_model=None)
async def password_reset_confirm_page(
    request: Request, token: str
) -> HTMLResponse | RedirectResponse:
    if not _local_only(request):
        return RedirectResponse("/login", status_code=303)
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
    if not _local_only(request):
        return RedirectResponse("/login", status_code=303)
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
    return RedirectResponse(_home_for(trainer), status_code=303)


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
    if trainer is None:
        return render(request, "auth.html", mode="login",
                      error="Correo o contraseña incorrectos.")
    if await session.get(TenantRow, trainer.tenant_id) is None:
        return render(
            request,
            "auth.html",
            mode="login",
            error=(
                "Tu cuenta quedó desincronizada con la base de datos. "
                "Regístrate de nuevo con otro correo."
            ),
        )
    _set_session(request, trainer)
    return RedirectResponse(_home_for(trainer), status_code=303)


@router.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request) -> HTMLResponse:
    return render(request, "auth.html", mode="signup", error=None)


@router.post("/signup", response_model=None)
async def signup(request: Request,
                 session: Annotated[AsyncSession, Depends(db_session)],
                 name: Annotated[str, Form()],
                 business_name: Annotated[str, Form()],
                 email: Annotated[str, Form()],
                 password: Annotated[str, Form()]) -> HTMLResponse | RedirectResponse:
    container = container_of(request)
    try:
        trainer = await signup_trainer(
            name=name, email=email, password=password, business_name=business_name,
            auth_repo=container.auth_repo(session),
            branding_dir=container.settings.branding_dir,
            admin_email=container.settings.admin_email,
        )
    except SignupError as exc:
        return render(request, "auth.html", mode="signup", error=str(exc))
    _set_session(request, trainer)
    return RedirectResponse(_home_for(trainer), status_code=303)


@router.post("/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
