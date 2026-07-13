"""Puerto de renderizado (Módulo 5).

Nota sobre el contrato: además de plan y branding, el renderer necesita
resolver food_id → nombre/unidad natural, así que recibe el catálogo de
alimentos del plan ya cargado (el caso de uso ExportPlan lo arma).
"""

from typing import Literal, Protocol
from uuid import UUID

from nutriplan.domain.models import Branding, FoodItem, MacroTargets, PlanCycle


class Renderer(Protocol):
    async def render(
        self,
        plan: PlanCycle,
        branding: Branding,
        foods: dict[UUID, FoodItem],
        fmt: Literal["pdf", "docx"],
        client_name: str | None = None,
        daily_targets: MacroTargets | None = None,
    ) -> bytes: ...
