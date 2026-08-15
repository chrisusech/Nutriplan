"""Seed local: tenant por defecto + usuario + base de alimentos curada.

Idempotente: UUIDs deterministas (uuid5) y upsert de alimentos. En Nivel 1
hay un solo tenant, pero todo el esquema ya es multi-tenant (ADR-02).
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import TenantRow
from nutriplan.adapters.db.repositories import SqlFoodRepository
from nutriplan.adapters.food.curated_loader import load_curated_foods

DEFAULT_TENANT_ID: UUID = uuid5(NAMESPACE_URL, "nutriplan/default-tenant")


async def seed_local(session: AsyncSession, foods_csv: Path) -> None:
    if await session.get(TenantRow, DEFAULT_TENANT_ID) is None:
        session.add(
            TenantRow(id=DEFAULT_TENANT_ID, name="Entrenadora local", created_at=datetime.now(UTC))
        )
    await session.flush()

    # Se re-siembra SIEMPRE. Antes había un guard por conteo (`if count >=
    # len(foods): return`) que hacía que corregir un macro, un meal_slot o una
    # porción en el CSV no llegara nunca a la DB si no cambiaba el NÚMERO de
    # filas: la base se quedaba con los datos viejos en silencio. `upsert_globals`
    # ya es idempotente (los ids son uuid5 del nombre) y unos cientos de upserts
    # al arrancar son milisegundos.
    foods = load_curated_foods(foods_csv)
    await SqlFoodRepository(session, DEFAULT_TENANT_ID).upsert_globals(foods)
