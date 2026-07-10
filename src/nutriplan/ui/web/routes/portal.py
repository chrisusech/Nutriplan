"""Portal del cliente (Workstream H): vista de solo lectura de su plan semanal.

Una cuenta con rol 'client' entra aquí y ve únicamente su plan aprobado y puede
descargar el PDF. No accede al resto de la app (lo garantiza el guard de app.py).
También expone el endpoint del entrenador para dar acceso a un cliente.
"""

import unicodedata
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.render.view import build_week
from nutriplan.application.auth import SignupError, create_client_access
from nutriplan.application.export_plan import export_plan
from nutriplan.domain.models import PlanCycle, PlanStatus
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import (
    client_id_of,
    container_of,
    db_session,
    render,
    repos_of,
    tenant_of,
)

router = APIRouter()


def _attachment(filename: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode() or "plan"
    )
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


async def _approved_plan(
    request: Request, session: AsyncSession, client_id: UUID
) -> PlanCycle | None:
    repos = repos_of(request, session)
    cycles = await repos.plans.list_for_client(client_id)
    for cycle in cycles:  # list_for_client viene DESC por created_at
        if cycle.status == PlanStatus.APPROVED:
            return cycle
    return None


@router.get("/portal", response_class=HTMLResponse)
async def portal_home(request: Request,
                      session: Annotated[AsyncSession, Depends(db_session)]) -> HTMLResponse:
    repos = repos_of(request, session)
    cid = client_id_of(request)
    client = await repos.clients.get(cid) if cid else None
    if client is None:
        return render(request, "portal.html", client=None, week=None, plan=None)

    plan = await _approved_plan(request, session, client.id)
    week = None
    subtitle = ""
    if plan is not None:
        food_ids = sorted(
            {p.food_id for d in plan.days for m in d.meals for p in m.portions}, key=str
        )
        foods = {f.id: f for f in await repos.foods.get_by_ids(food_ids)}
        week = build_week(plan, foods)
        targets = await repos.targets.get(plan.targets_id)
        if targets is not None:
            subtitle = f"{presenter.fmt_kcal(targets.daily.kcal)} kcal · 7 días"
    return render(request, "portal.html", client=client, week=week, plan=plan, subtitle=subtitle)


@router.get("/portal/plan.pdf")
async def portal_pdf(request: Request,
                     session: Annotated[AsyncSession, Depends(db_session)]) -> Response:
    container = container_of(request)
    repos = repos_of(request, session)
    cid = client_id_of(request)
    client = await repos.clients.get(cid) if cid else None
    plan = await _approved_plan(request, session, client.id) if client else None
    if client is None or plan is None:
        return RedirectResponse("/portal", status_code=303)

    _, content = await export_plan(
        plan_id=plan.id, fmt="pdf",
        plan_repo=repos.plans, food_repo=repos.foods, artifact_repo=repos.artifacts,
        renderer=container.renderer_for("pdf"), branding=container.branding(tenant_of(request)),
        exports_dir=container.settings.exports_dir, client_name=client.name,
    )
    who = client.name.replace(" ", "_")
    return Response(
        content=content, media_type="application/pdf",
        headers={"Content-Disposition": _attachment(f"plan_semanal_{who}.pdf")},
    )


# --- Lado del entrenador: dar acceso a un cliente ---------------------------


@router.post("/clientes/{cid}/acceso")
async def grant_access(request: Request,
                       session: Annotated[AsyncSession, Depends(db_session)],
                       cid: str,
                       email: Annotated[str, Form()],
                       password: Annotated[str, Form()]) -> RedirectResponse:
    """El entrenador crea el acceso de su cliente (rol 'client')."""
    container = container_of(request)
    repos = repos_of(request, session)
    client = await repos.clients.get(UUID(cid))
    if client is None:
        return RedirectResponse("/", status_code=303)
    try:
        await create_client_access(
            tenant_id=tenant_of(request), client_id=client.id, client_name=client.name,
            email=email, password=password, auth_repo=container.auth_repo(session),
        )
    except SignupError:
        pass  # el correo ya existe o es inválido; el entrenador reintenta
    return RedirectResponse(f"/generador?cliente={cid}", status_code=303)
