"""Motor y sesiones SQLAlchemy async con URL configurable (SQLite ↔ Postgres)."""

from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import AsyncAdaptedQueuePool

# Cuánto espera una escritura a que otra suelte el lock antes de rendirse.
SQLITE_BUSY_TIMEOUT_MS = 10_000

# El pool de Postgres, por instancia. Diez conexiones como techo duro: hay que
# poder multiplicarlo por el número de instancias y seguir cabiendo en el límite
# del pooler.
POOL_SIZE = 5
POOL_MAX_OVERFLOW = 5
POOL_RECYCLE_S = 300


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
        # (`prepared statement "__asyncpg_stmt_1__" does not exist`). Eso lo
        # arregla apagar los dos cachés de sentencias preparadas, y SOLO eso.
        #
        # Antes esto además usaba NullPool, y ahí estaba el problema: cada
        # petición abría un TCP+TLS nuevo contra el pooler. La pantalla de
        # generación hace polling cada ~900 ms y la de semana pide una receta
        # por comida, así que con cien personas eran cientos de conexiones
        # nuevas por segundo — se rompía la base mucho antes que la aplicación.
        # pgbouncer poolea del lado del servidor; que este lado reutilice las
        # suyas no le estorba.
        kwargs["poolclass"] = AsyncAdaptedQueuePool
        kwargs["pool_size"] = POOL_SIZE
        kwargs["max_overflow"] = POOL_MAX_OVERFLOW
        # Una conexión que el pooler ya cerró no puede descubrirse a mitad de
        # una petición de alguien.
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = POOL_RECYCLE_S
        kwargs["connect_args"] = {
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "timeout": 10,
            "command_timeout": 60,
        }
    engine = create_async_engine(database_url, **kwargs)
    if database_url.startswith("sqlite"):
        _tune_sqlite(engine)
    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
