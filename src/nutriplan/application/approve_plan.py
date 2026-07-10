"""Caso de uso ApprovePlan: compuerta humana DRAFT → APPROVED (+ audit_log).

Solo planes aprobados se exportan (ADR de la sección 14): la IA propone,
el código calcula, el humano firma.
"""

from uuid import UUID

import structlog

from nutriplan.domain.errors import GenerationError
from nutriplan.domain.models import PlanCycle, PlanStatus
from nutriplan.ports.job_repository import AuditLogRepository
from nutriplan.ports.repository import PlanRepository

logger = structlog.get_logger(__name__)


async def approve_plan(
    *,
    plan_id: UUID,
    plan_repo: PlanRepository,
    audit_repo: AuditLogRepository,
    approved_by: UUID | None = None,
) -> PlanCycle:
    plan = await plan_repo.get(plan_id)
    if plan is None:
        raise GenerationError(f"Plan {plan_id} no existe")
    if plan.status == PlanStatus.APPROVED:
        return plan  # idempotente: aprobar dos veces no es error
    if plan.status != PlanStatus.DRAFT:
        raise GenerationError(
            f"Solo un borrador puede aprobarse (estado actual: {plan.status.value})"
        )

    await plan_repo.set_status(plan_id, PlanStatus.APPROVED)
    await audit_repo.record(
        action="plan_approved",
        entity_type="plan_cycle",
        entity_id=plan_id,
        details={"approved_by": str(approved_by) if approved_by else None},
    )
    logger.info("plan_approved", plan_id=str(plan_id))
    approved = await plan_repo.get(plan_id)
    assert approved is not None
    return approved


async def reopen_plan(
    *,
    plan_id: UUID,
    plan_repo: PlanRepository,
    audit_repo: AuditLogRepository,
) -> PlanCycle:
    """Reabre un plan aprobado para corregirlo (APPROVED → DRAFT).

    El entrenador puede ajustar/quitar comidas y volver a aprobar; el historial
    de la aprobación anterior queda en audit_log.
    """
    plan = await plan_repo.get(plan_id)
    if plan is None:
        raise GenerationError(f"Plan {plan_id} no existe")
    if plan.status == PlanStatus.DRAFT:
        return plan
    await plan_repo.set_status(plan_id, PlanStatus.DRAFT)
    await audit_repo.record(
        action="plan_reopened", entity_type="plan_cycle", entity_id=plan_id, details={}
    )
    logger.info("plan_reopened", plan_id=str(plan_id))
    reopened = await plan_repo.get(plan_id)
    assert reopened is not None
    return reopened
