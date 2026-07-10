"""Dashboard de clientes (pantalla 1c) + color de marca."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.branding_store import save_branding
from nutriplan.domain.models import PlanStatus
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import container_of, db_session, render, repos_of

router = APIRouter()

FILTERS = [
    ("todos", "Todos"),
    ("listo", "Plan listo"),
    ("revision", "En revisión"),
    ("sin_plan", "Sin plan"),
]


async def _client_cards(request: Request, session: AsyncSession,
                        q: str, estado: str) -> list[dict[str, Any]]:
    repos = repos_of(request, session)
    cards = []
    for client in await repos.clients.list():
        if q and q.lower() not in client.name.lower():
            continue
        cycles = await repos.plans.list_for_client(client.id)
        targets = await repos.targets.latest_for_client(client.id)
        card = presenter.client_card(client, cycles, targets)
        if estado != "todos" and card["status"] is not presenter.STATUS_META.get(estado):
            continue
        cards.append(card)
    return cards


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request,
                    session: Annotated[AsyncSession, Depends(db_session)],
                    q: str = "", estado: str = "todos") -> HTMLResponse:
    repos = repos_of(request, session)
    clients = await repos.clients.list()

    listos = revision = 0
    for client in clients:
        cycles = await repos.plans.list_for_client(client.id)
        if any(c.status == PlanStatus.APPROVED for c in cycles):
            listos += 1
        elif cycles:
            revision += 1

    cards = await _client_cards(request, session, q, estado)
    stats = [
        {"icon": "group", "val": len(clients), "label": "Clientes activos",
         "color": "#F2704F", "soft": "#FCE9E3"},
        {"icon": "task_alt", "val": listos, "label": "Planes listos",
         "color": "#2E9E6E", "soft": "#E3F4EA"},
        {"icon": "pending_actions", "val": revision, "label": "Por revisar",
         "color": "#C9852E", "soft": "#FBF0DA"},
    ]
    is_htmx = request.headers.get("HX-Request")
    template = "partials/client_cards.html" if is_htmx else "dashboard.html"
    return render(request, template, active_tab="clientes", cards=cards, stats=stats,
                  filters=FILTERS, q=q, estado=estado, has_clients=bool(clients),
                  por_revisar=revision)


@router.post("/marca")
async def set_brand_color(request: Request, color: Annotated[str, Form()]) -> RedirectResponse:
    container = container_of(request)
    branding = container.branding()
    save_branding(container.settings.branding_dir,
                  branding.model_copy(update={"primary_color": color}))
    return RedirectResponse(request.headers.get("referer", "/"), status_code=303)
