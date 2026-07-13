"""Motor y sesiones SQLAlchemy async con URL configurable (SQLite ↔ Postgres)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos ORM."""


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    kwargs: dict[str, Any] = {"echo": echo}
    if database_url.startswith("postgresql+asyncpg"):
        # Supabase enruta por pgbouncer en modo transaction (puerto 6543): la
        # conexión cambia entre statements, así que los prepared statements que
        # asyncpg cachea por defecto se evaporan
        # (`prepared statement "__asyncpg_stmt_1__" does not exist`). Se apaga el
        # caché y se deja el pooling a pgbouncer.
        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {
            "statement_cache_size": 0,
            "timeout": 10,
            "command_timeout": 60,
        }
    return create_async_engine(database_url, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
