"""Seed local: tenant por defecto. El catálogo no se toca aquí."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import TenantRow

DEFAULT_TENANT_ID: UUID = uuid5(NAMESPACE_URL, "nutriplan/default-tenant")


async def seed_local(session: AsyncSession) -> None:
    if await session.get(TenantRow, DEFAULT_TENANT_ID) is None:
        session.add(
            TenantRow(id=DEFAULT_TENANT_ID, name="Entrenadora local", created_at=datetime.now(UTC))
        )
    await session.flush()
