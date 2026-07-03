"""Seed local: tenant por defecto + usuario + base de alimentos curada.

Idempotente: UUIDs deterministas (uuid5) y upsert de alimentos. En Nivel 1
hay un solo tenant, pero todo el esquema ya es multi-tenant (ADR-02).
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import TenantRow, UserRow
from nutriplan.adapters.db.repositories import SqlFoodRepository
from nutriplan.adapters.food.usda_importer import load_curated_foods

DEFAULT_TENANT_ID: UUID = uuid5(NAMESPACE_URL, "nutriplan/default-tenant")
DEFAULT_USER_ID: UUID = uuid5(NAMESPACE_URL, "nutriplan/default-user")


async def seed_local(session: AsyncSession, foods_csv: Path) -> None:
    if await session.get(TenantRow, DEFAULT_TENANT_ID) is None:
        session.add(
            TenantRow(
                id=DEFAULT_TENANT_ID, name="Entrenadora local", created_at=datetime.now(UTC)
            )
        )
    if await session.get(UserRow, DEFAULT_USER_ID) is None:
        session.add(UserRow(id=DEFAULT_USER_ID, tenant_id=DEFAULT_TENANT_ID, name="Valeria"))
    await session.flush()

    foods = load_curated_foods(foods_csv)
    await SqlFoodRepository(session, DEFAULT_TENANT_ID).upsert_globals(foods)
