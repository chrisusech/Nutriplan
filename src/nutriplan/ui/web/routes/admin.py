"""Módulo de administración (solo super_user).

Aquí el super_user ve las cuentas del BETA, les fija cupos (versiones
definitivas por plan) y los bloquea. El guard de /admin en app.py ya garantiza que
solo el super_user llega hasta acá.
"""

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import ClientRow, PlanCycleRow
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


@router.get("/admin/cuentas", response_class=HTMLResponse)
async def accounts_page(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)]) -> HTMLResponse:
    auth_repo = container_of(request).auth_repo(session)
    cuentas = await auth_repo.list_accounts()
    rows = [
        {
            "id": t.id,
            "name": t.name,
            "email": t.email,
            "is_active": t.is_active,
            "max_menus": t.max_menus,
        }
        for t in cuentas
    ]
    error = request.query_params.get("error")
    return render(request, "admin_cuentas.html", active_tab="cuentas",
                  cuentas=rows, error=error)


@router.post("/admin/cuentas")
async def create_account(request: Request,
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
        return RedirectResponse(f"/admin/cuentas?error={quote(str(exc))}", status_code=303)
    return RedirectResponse("/admin/cuentas", status_code=303)


@router.post("/admin/cuentas/{user_id}/limites")
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
    return RedirectResponse("/admin/cuentas", status_code=303)


@router.post("/admin/cuentas/{user_id}/bloquear")
async def block_account(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)],
                        user_id: str) -> RedirectResponse:
    await container_of(request).auth_repo(session).set_active(safe_uuid(user_id), False)
    return RedirectResponse("/admin/cuentas", status_code=303)


@router.post("/admin/cuentas/{user_id}/desbloquear")
async def unblock_account(request: Request,
                          session: Annotated[AsyncSession, Depends(db_session)],
                          user_id: str) -> RedirectResponse:
    await container_of(request).auth_repo(session).set_active(safe_uuid(user_id), True)
    return RedirectResponse("/admin/cuentas", status_code=303)


@router.get("/admin/metricas", response_class=HTMLResponse)
async def metrics(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse:
    """Lo que el BETA existe para averiguar.

    El guard de `app.py` ya comprobó `super_user` contra la base; aquí no se
    vuelve a confiar en la cookie.
    """
    repo = container_of(request).metrics_repo(session)
    return render(
        request,
        "admin_metricas.html",
        active_tab="metricas",
        funnel=await repo.funnel(),
        mejores=await repo.dishes_by_rating(best=True),
        peores=await repo.dishes_by_rating(best=False),
        objetivos=await repo.distribution(ClientRow.goal),
        ciudades=await repo.distribution(ClientRow.city),
        comidas=await repo.distribution(PlanCycleRow.model),
        habitos=await repo.eating_patterns(),
        comentarios=await repo.comments(),
        notas=await repo.rating_comments(),
        eventos=await repo.events_last_days(),
    )


@router.get("/admin/metricas.csv")
async def metrics_csv(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> Response:
    """Los platos y su nota, para analizarlos fuera."""
    repo = container_of(request).metrics_repo(session)
    filas = await repo.dishes_by_rating(best=True, limit=200)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=["plantilla", "media", "votos"])
    writer.writeheader()
    writer.writerows(filas)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="platos.csv"'},
    )
