"""Composition root (inyección de dependencias).

Único lugar del sistema que decide qué adaptadores concretos se usan según el
entorno (ENV=local → SQLite + render a filesystem; ENV=prod → Postgres, etc.).
Nada más en el código sabe qué implementación hay detrás de cada puerto.

El contenedor crece con cada módulo: por ahora arma settings, logging y la
fábrica de sesiones de DB; los repositorios, el LLMClient, el renderer y la
config nutricional se registran en los pasos siguientes del roadmap.
"""

from dataclasses import dataclass, field
from functools import cached_property

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nutriplan.adapters.db.session import create_engine, create_session_factory
from nutriplan.config.settings import Settings, get_settings
from nutriplan.observability.logging import configure_logging


@dataclass
class Container:
    settings: Settings = field(default_factory=get_settings)

    def __post_init__(self) -> None:
        configure_logging(self.settings.log_level)

    @cached_property
    def engine(self) -> AsyncEngine:
        return create_engine(self.settings.database_url)

    @cached_property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return create_session_factory(self.engine)


def build_container() -> Container:
    return Container()
