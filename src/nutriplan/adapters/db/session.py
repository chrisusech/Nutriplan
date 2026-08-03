"""Motor y sesiones SQLAlchemy async con URL configurable (SQLite ↔ Postgres)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

# Cuánto espera una escritura a que otra suelte el lock antes de rendirse.
SQLITE_BUSY_TIMEOUT_MS = 10_000


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos ORM."""


def _tune_sqlite(engine: AsyncEngine) -> None:
    """SQLite serializa las escrituras y por defecto falla en el acto si otra
    va en curso. Como los menús se generan en una tarea de fondo mientras la
    persona sigue usando la app, eso salía como `database is locked` en medio
    de una petición ajena. WAL deja leer durante una escritura; `busy_timeout`
    hace que la segunda escritura espere turno en vez de morir."""

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(dbapi_conn: Any, _record: Any) -> None:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


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
    engine = create_async_engine(database_url, **kwargs)
    if database_url.startswith("sqlite"):
        _tune_sqlite(engine)
    return engine


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
