"""Lo que ya tengo en casa: la nevera de ESTA semana.

No es "Mis alimentos" con otro nombre. Aquella pantalla contesta *"¿con qué
puedo armarte el menú?"* y vale para siempre; esta contesta *"¿qué tienes ya
comprado?"* y caduca al terminar la tira. Por eso se guarda contra el inicio
de la semana y no hay que venir a desmarcar nada: la semana que viene nace vacía.

Se ofrece solo lo que está en su menú: marcar algo que no come no significa nada.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.food_pool import resolve_allowed_foods
from nutriplan.domain.week import client_week_start
from nutriplan.ui.web.deps import account_id_of, db_session, render, repos_of
from nutriplan.ui.web.gate import week_gate
from nutriplan.ui.web.nav import safe_return
from nutriplan.ui.web.routes.foods import grouped

router = APIRouter()


@router.get("/tengo-en-casa", response_model=None)
async def pantry(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)

    plan = await repos.plans.get(client.active_plan_id) if client.active_plan_id else None
    semana = client_week_start(plan.week_start if plan else None)
    allowed = await resolve_allowed_foods(client, food_repo=repos.foods, client_repo=repos.clients)
    en_casa = await repos.clients.list_pantry_food_ids(client.id, semana)
    marcados = frozenset(str(fid) for fid in en_casa)
    volver = safe_return(request.query_params.get("volver"))
    return render(
        request,
        "tengo_en_casa.html",
        active_tab="semana",
        grupos=grouped(allowed, marked=marcados),
        n_marcados=len(marcados),
        volver=volver,
    )


@router.post("/tengo-en-casa", response_model=None)
async def save_pantry(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    food_id: Annotated[list[str] | None, Form()] = None,
) -> RedirectResponse:
    """Guarda la nevera entera de la semana, no un alimento suelto.

    El formulario manda TODAS las casillas marcadas de una vez, así que la lista
    vacía es una respuesta válida: significa "ya no tengo nada".
    """
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    if client is None:
        return RedirectResponse("/onboarding", status_code=303)

    ids: list[UUID] = []
    for raw in food_id or []:
        try:
            ids.append(UUID(raw))
        except ValueError:
            continue
    # Nadie puede meter en su nevera algo que su menú no contempla: la casilla
    # sale de esa lista, pero el formulario lo manda el navegador.
    allowed = {
        f.id
        for f in await resolve_allowed_foods(
            client, food_repo=repos.foods, client_repo=repos.clients
        )
    }
    plan = await repos.plans.get(client.active_plan_id) if client.active_plan_id else None
    semana = client_week_start(plan.week_start if plan else None)
    await repos.clients.set_pantry(client.id, semana, [fid for fid in ids if fid in allowed])
    await session.commit()
    form = await request.form()
    asked = form.get("volver")
    if isinstance(asked, str) and asked:
        return RedirectResponse(safe_return(asked), status_code=303)
    gate = await week_gate(request, session, client)
    destino = "/listo" if gate.closure.is_closed else "/"
    return RedirectResponse(destino, status_code=303)
