"""El laboratorio: probar el producto de verdad, sin puertas y sin ensuciar.

El super_user usa el flujo normal (`/`, `/menu/generar`, `/compra`, `/feedback`)
con su propia cuenta: `membership_of` lo devuelve ilimitado y `week_gate` no le
pide cerrar la semana. Sus datos quedan fuera de las métricas porque el
repositorio excluye sus tenants. Esta pantalla solo es la puerta de entrada, con
el estado real de la configuración a la vista.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    repos_of,
)

router = APIRouter()


@router.get("/admin/laboratorio", response_class=HTMLResponse)
async def lab(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse:
    container = container_of(request)
    repos = repos_of(request, session)
    settings = container.settings
    client = await repos.clients.get_by_user(account_id_of(request))
    planes = await repos.plans.list_for_client(client.id) if client else []
    return render(
        request,
        "admin_laboratorio.html",
        active_tab="laboratorio",
        perfil=client,
        planes=planes[:10],
        catalogo=container.meal_catalog.version,
        config_version=container.config_provider.get_nutrition_config().version,
        recetas_curadas=len(container.recipe_catalog.recipes),
        motor={
            "llm": container.llm_client is not None,
            "selecciona": settings.llm_select_foods,
            "refina": settings.llm_refine_names,
            "modelo": settings.llm_model_generate,
            "recetas_eager": settings.llm_eager_recipes,
        },
    )
