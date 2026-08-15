"""Buscar una cuenta para intervenir: conceder semanas, macros, activar."""

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.auth import SignupError, register
from nutriplan.application.membership import grant_free_trial, state_from_grants
from nutriplan.domain.models import Role
from nutriplan.ui.web.deps import container_of, db_session, render, safe_uuid

router = APIRouter()


@router.get("/admin", include_in_schema=False)
async def _admin_home() -> RedirectResponse:
    return RedirectResponse("/admin/metricas", status_code=303)


@router.get("/admin/clientes", include_in_schema=False)
async def _old_list() -> RedirectResponse:
    return RedirectResponse("/admin/metricas", status_code=303)


@router.get("/admin/usuarios", response_model=None)
async def users_page(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    """Sin búsqueda no se lista a nadie: eso era el padrón que pesaba."""
    q = (request.query_params.get("q") or "").strip()
    if not q and not request.query_params.get("error"):
        return RedirectResponse("/admin/metricas", status_code=303)
    container = container_of(request)
    admin_repo = container.admin_repo(session)
    rows = await admin_repo.overview(q=q) if q else []
    grants = await container.membership_repo(session).list_for_users([r.user_id for r in rows])
    weeks = await admin_repo.plan_weeks_for_clients([r.client_id for r in rows if r.client_id])
    now = datetime.now(UTC)
    filas = [
        {
            "cuenta": row,
            "membresia": state_from_grants(
                grants.get(row.user_id, []),
                week_starts=weeks.get(row.client_id, []) if row.client_id else [],
                now=now,
            ),
        }
        for row in rows
    ]
    return render(
        request,
        "admin_usuarios.html",
        active_tab="usuarios",
        usuarios=filas,
        q=q,
        error=request.query_params.get("error"),
    )


@router.post("/admin/usuarios")
async def create_account(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> RedirectResponse:
    """Alta manual. Recibe la misma semana de prueba que quien se registra solo."""
    container = container_of(request)
    try:
        account = await register(
            name=name,
            email=email,
            password=password,
            auth_repo=container.auth_repo(session),
            branding=container.branding_store,
            role=Role.USER,
        )
    except (SignupError, ValueError) as exc:
        return RedirectResponse(f"/admin/usuarios?error={quote(str(exc))}", status_code=303)
    await grant_free_trial(account=account, memberships=container.membership_repo(session))
    return RedirectResponse(f"/admin/usuarios?q={quote(email)}", status_code=303)


@router.post("/admin/usuarios/{user_id}/estado")
async def set_account_state(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user_id: str,
    activo: Annotated[str, Form()],
) -> RedirectResponse:
    """Activa o desactiva la cuenta.

    Desactivada no es expulsada: sigue pudiendo entrar, pero solo ve que su plan
    ya no está activo. Echarla del login la dejaba con un «correo o contraseña
    incorrectos» que no explicaba nada y llenaba el soporte.
    """
    auth = container_of(request).auth_repo(session)
    uid = safe_uuid(user_id)
    await auth.set_active(uid, activo == "1")
    cuenta = await auth.get_by_id(uid)
    if cuenta is not None and cuenta.email:
        request.app.state.account_cache.drop(cuenta.email)
    return RedirectResponse(f"/admin/usuarios/{user_id}", status_code=303)
