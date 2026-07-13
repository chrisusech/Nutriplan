"""Aplicar migraciones Alembic desde código.

El esquema lo define Alembic y solo Alembic. Antes lo definía
`Base.metadata.create_all` en el arranque, que parcheaba en silencio lo que a la
migración le faltaba — y así una migración incompleta sobrevivió sin que nadie
lo notara.
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config

PROJECT_ROOT = Path(__file__).resolve().parents[4]


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_to_head(database_url: str) -> None:
    """Corre Alembic en subproceso para no anidar asyncio.run() dentro de uvicorn."""
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )


def upgrade_to_head_inprocess(database_url: str) -> None:
    """Atajo síncrono para tests; evita el overhead del subproceso."""
    command.upgrade(alembic_config(database_url), "head")


async def upgrade_to_head_async(database_url: str) -> None:
    """Alembic es síncrono; se aparta a un hilo para no bloquear el event loop."""
    await asyncio.to_thread(upgrade_to_head, database_url)
