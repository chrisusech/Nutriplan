"""Las migraciones son la única fuente de verdad del esquema.

La migración anterior llevaba meses mintiendo (le faltaban columnas y una tabla
entera) y nadie se enteró porque la app parcheaba el esquema con
`Base.metadata.create_all` al arrancar. Este test cierra esa puerta: si alguien
cambia un modelo y no genera la migración, falla aquí.
"""

from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from nutriplan.adapters.db.session import Base

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_migrations_reproduce_the_models(tmp_path, monkeypatch):
    db = tmp_path / "migrated.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db}")

    command.upgrade(_alembic_config(f"sqlite+aiosqlite:///{db}"), "head")

    engine = create_engine(f"sqlite:///{db}")
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn)
        diff = compare_metadata(ctx, Base.metadata)
    engine.dispose()

    assert diff == [], (
        "El esquema migrado no coincide con los modelos. "
        "Corre `uv run alembic revision --autogenerate -m '<qué cambió>'`.\n"
        f"Diferencias: {diff}"
    )
