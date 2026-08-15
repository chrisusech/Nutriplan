"""Puerto de jobs persistidos (sección 14)."""

from datetime import datetime
from enum import StrEnum
from typing import Protocol
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

    async def touch(self, job_id: UUID) -> None:
        """Solo dice «sigo vivo», y solo si el job sigue corriendo.

        Es lo que separa el latido del resultado: un latido que llegue tarde no
        puede resucitar un job que ya terminó.
        """
        ...


class JobPrefixCounter(Protocol):
    """Contar intentos por prefijo de clave. Lo pide la puerta de la semana."""

    async def count_for_prefix(self, prefix: str) -> int: ...


class JobRepository(JobStatusSink, JobPrefixCounter, Protocol):
    async def add(self, job: Job) -> None: ...
    async def get(self, job_id: UUID) -> Job | None: ...
    async def get_by_idempotency_key(self, key: str) -> Job | None: ...

    async def claim(self, job_id: UUID, *, stale_before: datetime) -> bool:
        """Toma el job para ejecutarlo; True solo para quien gana la carrera.

        Un solo UPDATE condicional: con dos instancias detrás de un balanceador,
        las dos ven el mismo job encolado y las dos querrían lanzarlo.
        """
        ...
