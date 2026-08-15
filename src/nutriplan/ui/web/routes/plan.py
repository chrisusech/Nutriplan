"""Paywall: activar el plan mensual o anual tras la semana de prueba."""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.membership import grant_iap, membership_of
from nutriplan.config.settings import Environment
from nutriplan.domain.membership import IAP_ANNUAL_PRODUCT, IAP_MONTHLY_PRODUCT
from nutriplan.ui.web.deps import (
    account_id_of,
    acting_account,
    container_of,
    db_session,
    render,
    repos_of,
)

router = APIRouter()


@router.get("/plan", response_model=None)
async def paywall(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    container = container_of(request)
    account = await acting_account(request, session)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    state = await membership_of(
        account=account,
        client_id=client.id if client else None,
        memberships=container.membership_repo(session),
        plans=repos.plans,
    )
    return render(
        request,
        "plan.html",
        active_tab="perfil",
        membership=state,
        monthly=IAP_MONTHLY_PRODUCT,
        annual=IAP_ANNUAL_PRODUCT,
        error=request.query_params.get("error"),
    )


@router.post("/plan/activar", response_model=None)
async def activate_plan(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    product_id: Annotated[str, Form()],
    transaction_id: Annotated[str, Form()],
) -> RedirectResponse:
    """StoreKit (o el simulador local) confirma el cobro y aquí se concede.

    En prod un `local-` no vale: sería regalarse semanas sin pasar por Apple.
    """
    container = container_of(request)
    ref = transaction_id.strip()
    if container.settings.env is Environment.PROD and ref.startswith("local-"):
        return RedirectResponse(
            "/plan?error=" + quote("Esta compra no se pudo verificar."),
            status_code=303,
        )
    account = await acting_account(request, session)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    try:
        await grant_iap(
            account=account,
            memberships=container.membership_repo(session),
            product_id=product_id,
            transaction_id=ref,
        )
    except ValueError as exc:
        return RedirectResponse("/plan?error=" + quote(str(exc)), status_code=303)
    return RedirectResponse("/perfil", status_code=303)
