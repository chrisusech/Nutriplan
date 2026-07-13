"""Caso de uso ExportPlan: plan aprobado → PDF/DOCX con branding + artefacto.

El renderer es determinista (mismo plan → mismo archivo salvo fechas); el
artefacto queda registrado en export_artifacts con su ruta en filesystem.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import structlog

from nutriplan.adapters.render.view import plan_phases_in
from nutriplan.domain.errors import RenderError
from nutriplan.domain.models import Branding, MacroTargets, PlanStatus
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.job_repository import ArtifactRepository, ExportArtifact
from nutriplan.ports.renderer import Renderer
from nutriplan.ports.repository import PlanRepository

logger = structlog.get_logger(__name__)


async def export_plan(
    *,
    plan_id: UUID,
    fmt: Literal["pdf", "docx"],
    plan_repo: PlanRepository,
    food_repo: FoodRepository,
    artifact_repo: ArtifactRepository,
    renderer: Renderer,
    branding: Branding,
    exports_dir: Path,
    client_name: str | None = None,
    daily_targets: MacroTargets | None = None,
) -> tuple[ExportArtifact, bytes]:
    plan = await plan_repo.get(plan_id)
    if plan is None:
        raise RenderError(f"Plan {plan_id} no existe")
    if plan.status != PlanStatus.APPROVED:
        raise RenderError(
            f"Solo se exportan planes aprobados (estado actual: {plan.status.value})"
        )

    food_ids = {p.food_id for day in plan.days for meal in day.meals for p in meal.portions}
    foods = {f.id: f for f in await food_repo.get_by_ids(sorted(food_ids, key=str))}

    if plan.duration_days >= 30:
        phases = plan_phases_in(plan)
        expected_days = 7 * len(phases)
        if len(plan.days) < expected_days:
            raise RenderError(
                f"El plan es de 30 días pero solo tiene {len(plan.days)} días guardados "
                f"(se esperaban {expected_days}). Regenera con «Plan 30 días» y vuelve a aprobar."
            )

    content = await renderer.render(
        plan, branding, foods, fmt, client_name, daily_targets=daily_targets
    )

    exports_dir.mkdir(parents=True, exist_ok=True)
    path = exports_dir / f"plan_semanal_{plan_id}.{fmt}"
    path.write_bytes(content)

    artifact = ExportArtifact(
        id=uuid4(),
        tenant_id=plan.tenant_id,
        plan_cycle_id=plan_id,
        format=fmt,
        path=str(path),
        created_at=datetime.now(UTC),
    )
    await artifact_repo.add(artifact)
    logger.info("plan_exported", plan_id=str(plan_id), fmt=fmt, path=str(path))
    return artifact, content
