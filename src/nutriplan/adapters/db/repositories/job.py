"""Jobs de generación: el estado que la UI consulta."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, and_, func, or_, select, update
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

    async def count_for_prefix(self, prefix: str) -> int:
        """Los intentos que empiezan por esa clave. Los fallidos no cuentan:
        un job que reventó no le gastó a nadie su regeneración."""
        stmt = (
            select(func.count())
            .select_from(GenerationJobRow)
            .where(
                GenerationJobRow.tenant_id == self._tenant,
                GenerationJobRow.idempotency_key.startswith(prefix, autoescape=True),
                GenerationJobRow.status != JobStatus.FAILED.value,
            )
        )
        return int((await self._s.execute(stmt)).scalar() or 0)

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

    async def touch(self, job_id: UUID) -> None:
        stmt = (
            update(GenerationJobRow)
            .where(
                GenerationJobRow.id == job_id,
                GenerationJobRow.tenant_id == self._tenant,
                GenerationJobRow.status == JobStatus.RUNNING.value,
            )
            .values(updated_at=datetime.now(UTC))
        )
        await self._s.execute(stmt)
        await self._s.flush()

    async def claim(self, job_id: UUID, *, stale_before: datetime) -> bool:
        stmt = (
            update(GenerationJobRow)
            .where(
                GenerationJobRow.id == job_id,
                GenerationJobRow.tenant_id == self._tenant,
                or_(
                    GenerationJobRow.status.in_((JobStatus.QUEUED.value, JobStatus.FAILED.value)),
                    # Un `running` sin latido desde hace rato es un huérfano: el
                    # proceso que lo tenía se cayó y nadie va a volver a tocarlo.
                    and_(
                        GenerationJobRow.status == JobStatus.RUNNING.value,
                        GenerationJobRow.updated_at < stale_before,
                    ),
                ),
            )
            .values(status=JobStatus.RUNNING.value, error=None, updated_at=datetime.now(UTC))
        )
        result = cast("CursorResult[Any]", await self._s.execute(stmt))
        await self._s.flush()
        return int(result.rowcount or 0) > 0
