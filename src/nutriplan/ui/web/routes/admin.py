"""Módulo de administración (solo super_user).

Aquí el super_user da de alta entrenadores, les fija cupos (clientes y versiones
definitivas por plan) y los bloquea. El guard de /admin en app.py ya garantiza que
solo el super_user llega hasta acá.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.auth import SignupError, register
from nutriplan.domain.models import Role
from nutriplan.ui.web.deps import container_of, db_session, render, safe_uuid

router = APIRouter()


def _optional_int(raw: str) -> int | None:
    """Campo de cupo: vacío = sin límite (None); si no, entero >= 0."""
    raw = raw.strip()
    if not raw:
        return None
    return max(0, int(raw))


@router.get("/admin/entrenadores", response_class=HTMLResponse)
async def trainers_page(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)]) -> HTMLResponse:
    auth_repo = container_of(request).auth_repo(session)
    trainers = await auth_repo.list_accounts()
    rows = [
        {
            "id": t.id,
            "name": t.name,
            "email": t.email,
            "is_active": t.is_active,
            "max_menus": t.max_menus,
        }
        for t in trainers
    ]
    error = request.query_params.get("error")
    return render(request, "admin_trainers.html", active_tab="entrenadores",
                  trainers=rows, error=error)


@router.post("/admin/entrenadores")
async def create_trainer(request: Request,
                         session: Annotated[AsyncSession, Depends(db_session)],
                         name: Annotated[str, Form()],
                         email: Annotated[str, Form()],
                         password: Annotated[str, Form()],
                         max_menus: Annotated[str, Form()] = "") -> RedirectResponse:
    container = container_of(request)
    try:
        await register(
            name=name, email=email, password=password,
            auth_repo=container.auth_repo(session),
            branding_dir=container.settings.branding_dir, role=Role.USER,
            max_menus=_optional_int(max_menus),
        )
    except (SignupError, ValueError) as exc:
        from urllib.parse import quote
        return RedirectResponse(f"/admin/entrenadores?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/admin/entrenadores", status_code=303)


@router.post("/admin/entrenadores/{user_id}/limites")
async def update_limits(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)],
                        user_id: str,
                        max_menus: Annotated[str, Form()] = "") -> RedirectResponse:
    auth_repo = container_of(request).auth_repo(session)
    try:
        await auth_repo.set_limits(
            safe_uuid(user_id), max_menus=_optional_int(max_menus)
        )
    except ValueError:
        pass
    return RedirectResponse("/admin/entrenadores", status_code=303)


@router.post("/admin/entrenadores/{user_id}/bloquear")
async def block_trainer(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)],
                        user_id: str) -> RedirectResponse:
    await container_of(request).auth_repo(session).set_active(safe_uuid(user_id), False)
    return RedirectResponse("/admin/entrenadores", status_code=303)


@router.post("/admin/entrenadores/{user_id}/desbloquear")
async def unblock_trainer(request: Request,
                          session: Annotated[AsyncSession, Depends(db_session)],
                          user_id: str) -> RedirectResponse:
    await container_of(request).auth_repo(session).set_active(safe_uuid(user_id), True)
    return RedirectResponse("/admin/entrenadores", status_code=303)
