"""Jobs de generación: el estado que la UI consulta."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    GenerationJobRow,
)
from nutriplan.adapters.db.repositories._shared import _aware
from nutriplan.domain.errors import TenantIsolationError
from nutriplan.ports.job_repository import Job, JobStatus


class SqlJobRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: GenerationJobRow) -> Job:
        return Job(
            id=row.id,
            tenant_id=row.tenant_id,
            status=JobStatus(row.status),
            idempotency_key=row.idempotency_key,
            input_hash=row.input_hash,
            result_id=row.result_id,
            error=row.error,
            created_at=_aware(row.created_at),
            updated_at=_aware(row.updated_at),
        )

    async def add(self, job: Job) -> None:
        self._s.add(
            GenerationJobRow(
                id=job.id,
                tenant_id=self._tenant,
                status=job.status.value,
                idempotency_key=job.idempotency_key,
                input_hash=job.input_hash,
                result_id=job.result_id,
                error=job.error,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )
        )
        await self._s.flush()

    async def get(self, job_id: UUID) -> Job | None:
        stmt = select(GenerationJobRow).where(
            GenerationJobRow.id == job_id, GenerationJobRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_idempotency_key(self, key: str) -> Job | None:
        stmt = select(GenerationJobRow).where(
            GenerationJobRow.idempotency_key == key, GenerationJobRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def update(self, job: Job) -> None:
        stmt = select(GenerationJobRow).where(
            GenerationJobRow.id == job.id, GenerationJobRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise TenantIsolationError("Job inexistente para este tenant")
        row.status = job.status.value
        row.input_hash = job.input_hash
        row.result_id = job.result_id
        row.error = job.error
        row.updated_at = datetime.now(UTC)
        await self._s.flush()
