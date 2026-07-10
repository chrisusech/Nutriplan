"""Alta y login de entrenadores (Workstream G). Sesión por cookie firmada."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.auth import SignupError, authenticate, signup_trainer
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


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    return render(request, "auth.html", mode="login", error=None)


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
