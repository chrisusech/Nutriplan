"""Puerto de jobs persistidos y artefactos exportados (sección 14)."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel


class JobKind(StrEnum):
    GENERATE = "generate"
    EXPORT = "export"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    id: UUID
    tenant_id: UUID
    kind: JobKind
    status: JobStatus = JobStatus.QUEUED
    idempotency_key: str
    input_hash: str | None = None
    result_id: UUID | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class ExportArtifact(BaseModel):
    id: UUID
    tenant_id: UUID
    plan_cycle_id: UUID
    format: str  # "pdf" | "docx"
    path: str
    created_at: datetime


class JobRepository(Protocol):
    async def add(self, job: Job) -> None: ...
    async def get(self, job_id: UUID) -> Job | None: ...
    async def get_by_idempotency_key(self, key: str) -> Job | None: ...
    async def update(self, job: Job) -> None: ...


class ArtifactRepository(Protocol):
    async def add(self, artifact: ExportArtifact) -> None: ...
    async def list_for_plan(self, plan_cycle_id: UUID) -> list[ExportArtifact]: ...


class AuditLogRepository(Protocol):
    async def record(
        self, *, action: str, entity_type: str, entity_id: UUID, details: dict | None = None
    ) -> None: ...
