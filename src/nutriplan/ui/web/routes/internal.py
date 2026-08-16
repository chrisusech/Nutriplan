"""Rutas de máquina a máquina: el tick de la semana y las notificaciones de Apple."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.auto_week import run_auto_week
from nutriplan.application.iap import apply_store_notification
from nutriplan.ports.iap import IAPError
from nutriplan.ui.web.deps import container_of, db_session

router = APIRouter()


@router.post("/internal/tick", response_model=None)
async def tick(request: Request) -> JSONResponse | PlainTextResponse:
    """Dispara el auto-generate. Vacío el secret = la ruta no existe."""
    secret = container_of(request).settings.internal_tick_secret
    given = request.headers.get("X-Tick-Secret", "")
    if not secret or given != secret:
        return PlainTextResponse("Not found", status_code=404)
    n = await run_auto_week(container_of(request), force=True)
    return JSONResponse({"generated": n})


@router.post("/internal/app-store", response_model=None)
async def app_store_notification(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
) -> JSONResponse | PlainTextResponse:
    """Notificaciones V2 de App Store. Apple firma el payload; no hay secreto."""
    try:
        body = await request.json()
    except Exception:
        return PlainTextResponse("Bad request", status_code=400)
    signed = body.get("signedPayload") if isinstance(body, dict) else None
    if not isinstance(signed, str) or not signed.strip():
        return PlainTextResponse("Bad request", status_code=400)
    container = container_of(request)
    try:
        notification = container.iap_verifier.verify_notification(signed.strip())
        status = await apply_store_notification(
            notification=notification,
            memberships=container.membership_repo(session),
            accounts=container.auth_repo(session),
            now=datetime.now(UTC),
        )
    except IAPError:
        return JSONResponse({"status": "ignored"})
    return JSONResponse({"status": status})
