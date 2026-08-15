"""Rutas de máquina a máquina: el tick de la semana automática."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from nutriplan.application.auto_week import run_auto_week
from nutriplan.ui.web.deps import container_of

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
