"""La ficha de un usuario: su historia y las dos palancas del negocio.

Conceder semanas (activar el plan cuando paga) y ajustar kcal a mano. Ambas
escriben en las tablas que ya existen — `membership_grants` y `nutrition_targets`
—, así que lo que se toca aquí es exactamente lo que la app lee.
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.membership import grant_weeks, membership_of
from nutriplan.container import Container
from nutriplan.domain.adapt_targets import ADMIN_LOCK
from nutriplan.domain.calculation import compute_targets, energy_kcal
from nutriplan.domain.errors import CalculationError
from nutriplan.domain.membership import DEFAULT_PLAN_DAYS, DEFAULT_PLAN_WEEKS
from nutriplan.domain.models import Account, Client, MacroFormula, NutritionTargets
from nutriplan.domain.week_recap import week_recap
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    safe_uuid,
)

router = APIRouter()

MIN_KCAL = 1000
MAX_KCAL = 6000


async def _account_or_404(request: Request, session: AsyncSession, user_id: str) -> Account:
    account = await container_of(request).auth_repo(session).get_by_id(safe_uuid(user_id))
    if account is None:
        raise HTTPException(status_code=404, detail="Esa cuenta no existe")
    return account


@router.get("/admin/clientes/{user_id}", include_in_schema=False)
async def _old_detail(user_id: str) -> RedirectResponse:
    return RedirectResponse(f"/admin/usuarios/{user_id}", status_code=303)


@router.get("/admin/usuarios/{user_id}", response_class=HTMLResponse)
async def user_detail(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user_id: str,
) -> HTMLResponse:
    """Todo lo de una persona: peso, macros, semanas, notas y concesiones."""
    container = container_of(request)
    account = await _account_or_404(request, session, user_id)
    repos = container.repos(session, account.tenant_id)
    memberships = container.membership_repo(session)

    client = await repos.clients.get_by_user(account.id)
    estado = await membership_of(
        account=account,
        client_id=client.id if client else None,
        memberships=memberships,
        plans=repos.plans,
    )
    guardados = await repos.targets.latest_for_client(client.id) if client else None
    targets = guardados
    if client is not None and guardados is None:
        targets = _preview(client, container)
    planes = await repos.plans.list_for_client(client.id) if client else []
    activo = next((p for p in planes if client and p.id == client.active_plan_id), None)
    recap = week_recap(activo, targets.daily) if activo and targets else None
    return render(
        request,
        "admin_usuario.html",
        active_tab="usuarios",
        cuenta=account,
        perfil=client,
        membresia=estado,
        concesiones=await memberships.list_for_user(account.id),
        targets=targets,
        es_estimacion=guardados is None,
        bloqueo=bool((targets.overrides or {}).get(ADMIN_LOCK)) if targets else False,
        pesajes=await repos.weights.history(client.id) if client else [],
        planes=planes,
        recap=recap,
        semanas_por_defecto=DEFAULT_PLAN_WEEKS,
        dias_por_defecto=DEFAULT_PLAN_DAYS,
        error=request.query_params.get("error"),
    )


@router.post("/admin/usuarios/{user_id}/semanas")
async def add_weeks(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user_id: str,
    semanas: Annotated[int, Form()] = DEFAULT_PLAN_WEEKS,
    dias: Annotated[int, Form()] = DEFAULT_PLAN_DAYS,
    nota: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Activa el plan: cuatro semanas y treinta días, o lo que se pacte."""
    account = await _account_or_404(request, session, user_id)
    await grant_weeks(
        account=account,
        memberships=container_of(request).membership_repo(session),
        weeks=max(1, min(int(semanas), 52)),
        days_valid=max(1, min(int(dias), 365)),
        granted_by=account_id_of(request),
        note=nota.strip()[:300] or None,
    )
    return RedirectResponse(f"/admin/usuarios/{user_id}", status_code=303)


@router.post("/admin/usuarios/{user_id}/macros")
async def set_macros(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    user_id: str,
    proteina_g: Annotated[float, Form()],
    carbo_g: Annotated[float, Form()],
    grasa_g: Annotated[float, Form()],
    bloquear: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Ajusta los macros a mano escribiendo una fila nueva de targets.

    Llegan los tres macros en gramos y las kcal salen de ellos: son su
    consecuencia (4·P + 4·C + 9·G), nunca un cuarto número suelto. La pantalla
    deja escribir las kcal y recalcula el carbohidrato mientras se teclea, así
    que se ajusta por donde se quiera y lo que llega aquí ya es coherente.

    Se guarda como fórmula (g/kg + kcal), no como override en gramos: así el
    ajuste sobrevive al check-in semanal, que sube o baja las kcal sobre esa
    misma fórmula. Un override en gramos congelaría los macros y anularía la
    adaptación.

    No se edita la fila anterior: la tabla es append-only y ese historial es lo
    que permite explicar después por qué alguien come lo que come.
    """
    container = container_of(request)
    account = await _account_or_404(request, session, user_id)
    repos = container.repos(session, account.tenant_id)
    client = await repos.clients.get_by_user(account.id)
    if client is None:
        return _back(user_id, "Todavía no tiene perfil")
    if proteina_g <= 0 or grasa_g <= 0 or carbo_g < 0:
        return _back(user_id, "La proteína y la grasa tienen que ser mayores que cero.")

    kcal = energy_kcal(proteina_g, carbo_g, grasa_g)
    if not MIN_KCAL <= kcal <= MAX_KCAL:
        return _back(
            user_id,
            f"Esos macros dan {kcal:.0f} kcal y el rango permitido es {MIN_KCAL}–{MAX_KCAL}.",
        )

    previous = await repos.targets.latest_for_client(client.id)
    overrides = dict(previous.overrides or {}) if previous else {}
    if bloquear == "1":
        overrides[ADMIN_LOCK] = 1.0
    else:
        overrides.pop(ADMIN_LOCK, None)

    try:
        await compute_and_store_targets(
            client=client,
            config_provider=container.config_provider,
            targets_repo=repos.targets,
            formula=MacroFormula(
                protein_g_per_kg=round(proteina_g / client.weight_kg, 4),
                fat_g_per_kg=round(grasa_g / client.weight_kg, 4),
                kcal_override=kcal,
            ),
            overrides=overrides or None,
        )
    except CalculationError as exc:
        return _back(user_id, str(exc))
    return RedirectResponse(f"/admin/usuarios/{user_id}", status_code=303)


def _back(user_id: str, error: str) -> RedirectResponse:
    return RedirectResponse(f"/admin/usuarios/{user_id}?error={quote(error)}", status_code=303)


def _preview(client: Client, container: Container) -> NutritionTargets | None:
    """Lo que el motor calcularía hoy para esta persona, sin guardar nada."""
    try:
        return compute_targets(client, container.nutrition_config(client))
    except CalculationError:
        return None
