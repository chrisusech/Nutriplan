"""Bitacora de acciones."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AuditLogRow,
)


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes naive; se asumen UTC para round-trips estables."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class SqlAuditLogRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: UUID,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._s.add(
            AuditLogRow(
                tenant_id=self._tenant,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details or {},
                at=datetime.now(UTC),
            )
        )
        await self._s.flush()
