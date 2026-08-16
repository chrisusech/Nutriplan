"""Paywall: activar el plan mensual o anual tras la semana de prueba."""

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.iap import activate_verified_purchase
from nutriplan.application.membership import grant_iap, membership_of
from nutriplan.config.settings import Environment
from nutriplan.domain.membership import IAP_ANNUAL_PRODUCT, IAP_MONTHLY_PRODUCT
from nutriplan.ports.iap import IAPError
from nutriplan.ui.web.deps import (
    account_id_of,
    acting_account,
    container_of,
    db_session,
    render,
    repos_of,
)
from nutriplan.ui.web.public_errors import PURCHASE_FAILED, sanitize_public_error

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
        error=sanitize_public_error(request.query_params.get("error") or ""),
    )


@router.post("/plan/activar", response_model=None)
async def activate_plan(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    product_id: Annotated[str, Form()] = "",
    transaction_id: Annotated[str, Form()] = "",
    signed_transaction: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """StoreKit confirma el cobro con un JWS. Un id suelto no vale en prod.

    En local, y solo ahí, un `local-…` sirve para el TestClient y Safari en el
    Mac. El iPhone nativo nunca fabrica esa cadena: o hay JWS o no hay compra.
    """
    failed = RedirectResponse("/plan?error=" + quote(PURCHASE_FAILED), status_code=303)
    container = container_of(request)
    account = await acting_account(request, session)
    if account is None:
        return RedirectResponse("/login", status_code=303)
    signed = signed_transaction.strip()
    memberships = container.membership_repo(session)
    try:
        if signed:
            purchase = container.iap_verifier.verify_transaction(signed)
            await activate_verified_purchase(
                account=account,
                memberships=memberships,
                purchase=purchase,
                now=datetime.now(UTC),
            )
            return RedirectResponse("/perfil", status_code=303)
        ref = transaction_id.strip()
        if container.settings.env is Environment.LOCAL and ref.startswith("local-"):
            await grant_iap(
                account=account,
                memberships=memberships,
                product_id=product_id,
                transaction_id=ref,
            )
            return RedirectResponse("/perfil", status_code=303)
    except (IAPError, ValueError):
        return failed
    return failed
