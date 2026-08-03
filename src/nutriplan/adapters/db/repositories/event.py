"""Eventos de uso. Sin PII: lo que identifica vive en las otras tablas."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import AppEventRow


class SqlEventRepository:
    """Escribe en `app_events`. No filtra por tenant al leer: el super_user
    mira el agregado de todos, que es el producto del BETA."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(
        self,
        *,
        name: str,
        props: dict[str, Any],
        user_id: UUID | None,
        tenant_id: UUID | None,
        platform: str | None,
    ) -> None:
        self._s.add(
            AppEventRow(
                name=name,
                props=props,
                user_id=user_id,
                tenant_id=tenant_id,
                platform=platform,
                at=datetime.now(UTC),
            )
        )
        await self._s.flush()
