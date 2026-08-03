"""Puerto de jobs persistidos (sección 14)."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    id: UUID
    tenant_id: UUID
    status: JobStatus = JobStatus.QUEUED
    idempotency_key: str
    input_hash: str | None = None
    result_id: UUID | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class JobStatusSink(Protocol):
    """Solo anunciar en qué va el job.

    Es lo único que la tarea de fondo necesita, y pedir menos es lo que permite
    escribir el progreso en una transacción aparte de la del plan.
    """

    async def update(self, job: Job) -> None: ...


class JobRepository(JobStatusSink, Protocol):
    async def add(self, job: Job) -> None: ...
    async def get(self, job_id: UUID) -> Job | None: ...
    async def get_by_idempotency_key(self, key: str) -> Job | None: ...


class AuditLogRepository(Protocol):
    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: UUID,
        details: dict[str, Any] | None = None,
    ) -> None: ...
