"""Composition root (inyección de dependencias).

Único lugar del sistema que decide qué adaptadores concretos se usan según el
entorno (ENV=local → SQLite + render a filesystem; ENV=prod → Postgres, etc.).
Nada más en el código sabe qué implementación hay detrás de cada puerto.

Los repositorios son por-sesión (cada request/job abre la suya vía
`session_factory` y arma su bundle con `repos(session)`); lo demás es
compartido y cacheado.
"""

from dataclasses import dataclass, field
from functools import cached_property
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from nutriplan.adapters.branding_store import load_branding
from nutriplan.adapters.config_yaml import YamlConfigProvider
from nutriplan.adapters.db.repositories import (
    SqlArtifactRepository,
    SqlAuditLogRepository,
    SqlAuthRepository,
    SqlClientRepository,
    SqlFoodRepository,
    SqlIntakeRepository,
    SqlJobRepository,
    SqlPlanRepository,
    SqlRecipeRepository,
    SqlTargetsRepository,
)
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.db.session import create_engine, create_session_factory
from nutriplan.adapters.meals.template_store import cached_meal_catalog
from nutriplan.adapters.render.docx_renderer import DocxRenderer
from nutriplan.adapters.render.pdf_weasyprint import WeasyPrintRenderer
from nutriplan.config.settings import Settings, get_settings
from nutriplan.domain.meal_template import MealCatalog
from nutriplan.domain.models import Branding
from nutriplan.observability.logging import configure_logging
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.renderer import Renderer


@dataclass
class Repos:
    """Bundle de repositorios de una sesión (todos con el tenant filtrado)."""

    clients: SqlClientRepository
    foods: SqlFoodRepository
    intakes: SqlIntakeRepository
    targets: SqlTargetsRepository
    plans: SqlPlanRepository
    jobs: SqlJobRepository
    artifacts: SqlArtifactRepository
    audit: SqlAuditLogRepository
    recipes: SqlRecipeRepository


@dataclass
class Container:
    settings: Settings = field(default_factory=get_settings)
    tenant_id: UUID = DEFAULT_TENANT_ID

    def __post_init__(self) -> None:
        configure_logging(self.settings.log_level)

    @cached_property
    def engine(self) -> AsyncEngine:
        return create_engine(self.settings.database_url)

    @cached_property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return create_session_factory(self.engine)

    def repos(self, session: AsyncSession, tenant_id: UUID | None = None) -> Repos:
        t = tenant_id or self.tenant_id
        return Repos(
            clients=SqlClientRepository(session, t),
            foods=SqlFoodRepository(session, t),
            intakes=SqlIntakeRepository(session, t),
            targets=SqlTargetsRepository(session, t),
            plans=SqlPlanRepository(session, t),
            jobs=SqlJobRepository(session, t),
            artifacts=SqlArtifactRepository(session, t),
            audit=SqlAuditLogRepository(session, t),
            recipes=SqlRecipeRepository(session, t),
        )

    def auth_repo(self, session: AsyncSession) -> SqlAuthRepository:
        return SqlAuthRepository(session)

    def admin_recipe_repo(self, session: AsyncSession) -> SqlRecipeRepository:
        """Repo de recetas sin filtro de tenant (solo rutas de admin)."""
        return SqlRecipeRepository(session, None)

    @cached_property
    def config_provider(self) -> YamlConfigProvider:
        return YamlConfigProvider(self.settings.nutrition_config_path)

    @cached_property
    def meal_catalog(self) -> MealCatalog:
        """El catálogo de platos. Un YAML mal formado revienta aquí, al arrancar."""
        return cached_meal_catalog(
            self.settings.food_classes_path, self.settings.meal_templates_path
        )

    @cached_property
    def llm_client(self) -> LLMClient | None:
        """AnthropicClient si hay API key; None = modo offline (heurístico)."""
        if not self.settings.anthropic_api_key:
            return None
        from nutriplan.adapters.llm.anthropic_client import AnthropicClient

        return AnthropicClient(self.settings.anthropic_api_key)

    @cached_property
    def pdf_renderer(self) -> Renderer:
        return WeasyPrintRenderer()

    @cached_property
    def docx_renderer(self) -> Renderer:
        return DocxRenderer()

    def renderer_for(self, fmt: str) -> Renderer:
        return self.pdf_renderer if fmt == "pdf" else self.docx_renderer

    def branding(self, tenant_id: UUID | None = None) -> Branding:
        """Marca del entrenador (por tenant). Sin caché: el selector de color de
        la UI la puede cambiar en vivo."""
        tenant = str(tenant_id) if tenant_id else "default"
        return load_branding(self.settings.branding_dir, tenant)


def build_container() -> Container:
    return Container()
