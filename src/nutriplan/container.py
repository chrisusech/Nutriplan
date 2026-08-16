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

from nutriplan.adapters.branding_store import FileBrandingStore, load_branding
from nutriplan.adapters.config_yaml import YamlConfigProvider
from nutriplan.adapters.db.repositories import (
    SqlAccountEraser,
    SqlAdminRepository,
    SqlAuthRepository,
    SqlClientRepository,
    SqlDeviceTokenRepository,
    SqlDishRecipeRepository,
    SqlEventRepository,
    SqlFoodRepository,
    SqlJobRepository,
    SqlMembershipRepository,
    SqlMetricsExportRepository,
    SqlMetricsRepository,
    SqlMetricsVoicesRepository,
    SqlPasswordResetRepository,
    SqlPlanRepository,
    SqlRatingRepository,
    SqlTargetsRepository,
    SqlTasteSignalRepository,
    SqlWeightRepository,
)
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.db.session import create_engine, create_session_factory
from nutriplan.adapters.email import ConsoleEmailSender, SmtpEmailSender
from nutriplan.adapters.iap.apple_jws import AppleJWSVerifier
from nutriplan.adapters.llm.offline_engine import build_offline_engine
from nutriplan.adapters.meals.recipe_catalog_store import cached_recipe_catalog
from nutriplan.adapters.meals.restaurant_store import cached_restaurant_catalog
from nutriplan.adapters.meals.template_store import cached_meal_catalog
from nutriplan.config.settings import Environment, Settings, get_settings
from nutriplan.domain.meal_template import MealCatalog
from nutriplan.domain.models import Branding, Client
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.recipe_catalog import RecipeCatalog
from nutriplan.domain.restaurant import RestaurantCatalog
from nutriplan.observability.logging import configure_logging
from nutriplan.ports.branding import BrandingStore
from nutriplan.ports.email_sender import EmailSender
from nutriplan.ports.iap import StoreTransactionVerifier
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.plan_selector import OfflineEngineFactory


@dataclass
class Repos:
    """Bundle de repositorios de una sesión (todos con el tenant filtrado)."""

    clients: SqlClientRepository
    foods: SqlFoodRepository
    targets: SqlTargetsRepository
    plans: SqlPlanRepository
    jobs: SqlJobRepository
    ratings: SqlRatingRepository
    weights: SqlWeightRepository
    taste: SqlTasteSignalRepository


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
            targets=SqlTargetsRepository(session, t),
            plans=SqlPlanRepository(session, t),
            jobs=SqlJobRepository(session, t),
            ratings=SqlRatingRepository(session, t),
            weights=SqlWeightRepository(session, t),
            taste=SqlTasteSignalRepository(session, t),
        )

    def membership_repo(self, session: AsyncSession) -> SqlMembershipRepository:
        """Sin tenant: el super_user concede semanas a cuentas que no son suyas."""
        return SqlMembershipRepository(session)

    @cached_property
    def iap_verifier(self) -> StoreTransactionVerifier:
        """Apple firma el JWS; Sandbox y Production valen en cualquier ENV."""
        return AppleJWSVerifier()

    def account_eraser(self, session: AsyncSession) -> SqlAccountEraser:
        return SqlAccountEraser(session)

    def metrics_repo(self, session: AsyncSession) -> SqlMetricsRepository:
        """Cruza tenants: solo para el super_user, y solo agregados."""
        return SqlMetricsRepository(session)

    def metrics_export_repo(self, session: AsyncSession) -> SqlMetricsExportRepository:
        """Lo que la app sirvió, fila a fila, para el informe descargable."""
        return SqlMetricsExportRepository(session)

    def metrics_voices_repo(self, session: AsyncSession) -> SqlMetricsVoicesRepository:
        """Lo que la gente escribió, para el mismo informe."""
        return SqlMetricsVoicesRepository(session)

    def admin_repo(self, session: AsyncSession) -> SqlAdminRepository:
        """Cruza tenants con datos de personas: solo detrás del guard de /admin."""
        return SqlAdminRepository(session)

    def event_repo(self, session: AsyncSession) -> SqlEventRepository:
        """Sin tenant: el embudo se lee agregado."""
        return SqlEventRepository(session)

    def dish_recipe_repo(self, session: AsyncSession) -> SqlDishRecipeRepository:
        """Sin tenant: la caché de recetas es de todos."""
        return SqlDishRecipeRepository(session)

    def auth_repo(self, session: AsyncSession) -> SqlAuthRepository:
        return SqlAuthRepository(session)

    def password_reset_repo(self, session: AsyncSession) -> SqlPasswordResetRepository:
        """Sin tenant: quien recupera su contraseña todavía no tiene sesión."""
        return SqlPasswordResetRepository(session)

    def device_token_repo(self, session: AsyncSession) -> SqlDeviceTokenRepository:
        return SqlDeviceTokenRepository(session)

    @cached_property
    def config_provider(self) -> YamlConfigProvider:
        return YamlConfigProvider(self.settings.nutrition_config_path)

    def nutrition_config(self, client: Client) -> NutritionConfig:
        """La estrategia YA AJUSTADA a las comidas que hace este cliente.

        Es el único sitio donde se aplica: todo lo demás (porcionador, validación,
        motores, presenter) lee `config.meal_distribution`, así que recortar el
        reparto aquí hace que el plan entero salga con las comidas del cliente sin
        que ninguno de ellos sepa nada de esto.
        """
        return self.config_provider.get_nutrition_config().for_slots(client.meal_slots)

    @cached_property
    def meal_catalog(self) -> MealCatalog:
        """El catálogo de platos. Un YAML mal formado revienta aquí, al arrancar."""
        return cached_meal_catalog(
            self.settings.food_classes_path, self.settings.meal_templates_path
        )

    @cached_property
    def recipe_catalog(self) -> RecipeCatalog:
        """Recetas curadas a mano. Mal YAML = fallo al arrancar."""
        return cached_recipe_catalog(str(self.settings.recipes_catalog_path))

    def reload_recipe_catalog(self) -> RecipeCatalog:
        """Relee el YAML tras promover una receta desde la consola.

        Sin releerlo, la receta recién curada no se usaría hasta el siguiente
        reinicio.
        """
        self.__dict__.pop("recipe_catalog", None)
        return self.recipe_catalog

    @cached_property
    def restaurant_catalog(self) -> RestaurantCatalog:
        """Platos de calle. Mal YAML = fallo al arrancar."""
        return cached_restaurant_catalog(str(self.settings.restaurants_catalog_path))

    def reload_restaurant_catalog(self) -> RestaurantCatalog:
        """Relee el YAML tras añadir un restaurante o un plato desde la consola."""
        self.__dict__.pop("restaurant_catalog", None)
        return self.restaurant_catalog

    @cached_property
    def branding_store(self) -> BrandingStore:
        return FileBrandingStore(self.settings.branding_dir)

    @property
    def offline_engine(self) -> OfflineEngineFactory:
        """Quién arma la semana cuando la IA no está (o falla)."""
        return build_offline_engine

    @cached_property
    def llm_client(self) -> LLMClient | None:
        """La cadena de proveedores, en orden de preferencia.

        `None` = modo offline: la generación cae al `TemplateSelector`
        determinista y el crítico se salta. La app funciona igual, solo que
        con menos sabor.
        """
        from nutriplan.adapters.llm.fallback_client import FallbackLLMClient
        from nutriplan.adapters.llm.model_pin import ModelPinnedClient
        from nutriplan.adapters.llm.openai_compat_client import OpenAICompatClient

        chain: list[tuple[str, LLMClient]] = []
        s = self.settings

        def _usable_key(value: str) -> bool:
            # Evita montar un fallback con placeholders del .env (`…`, `...`).
            v = value.strip()
            return bool(v) and not v.startswith(("…", "...", "YOUR_", "tu_"))

        if s.llm_base_url and _usable_key(s.llm_api_key):
            chain.append(
                (
                    "primary",
                    OpenAICompatClient(
                        s.llm_api_key,
                        base_url=s.llm_base_url,
                        max_tokens_select=8192,
                        budget_s=90.0,
                    ),
                )
            )
        if s.llm_fallback_base_url and _usable_key(s.llm_fallback_api_key):
            fallback: LLMClient = OpenAICompatClient(
                s.llm_fallback_api_key,
                base_url=s.llm_fallback_base_url,
                max_tokens_select=8192,
                budget_s=90.0,
            )
            # Sin pin, el call site manda el modelo del primario (Groq) a Gemini
            # Free y la cuota de reserva nunca llega a usarse.
            if s.llm_fallback_model.strip():
                fallback = ModelPinnedClient(fallback, s.llm_fallback_model)
            chain.append(("fallback", fallback))
        if s.anthropic_api_key:
            from nutriplan.adapters.llm.anthropic_client import AnthropicClient

            chain.append(("anthropic", AnthropicClient(s.anthropic_api_key)))

        if not chain:
            return None
        return chain[0][1] if len(chain) == 1 else FallbackLLMClient(chain)

    @cached_property
    def mailer(self) -> EmailSender:
        """SMTP si está configurado; si no, consola (enlace por stderr en local)."""
        if self.settings.smtp_host:
            return SmtpEmailSender(
                host=self.settings.smtp_host,
                port=self.settings.smtp_port,
                username=self.settings.smtp_user,
                password=self.settings.smtp_password,
                sender=self.settings.smtp_from,
            )
        return ConsoleEmailSender(dump_body=self.settings.env is not Environment.PROD)

    def branding(self, tenant_id: UUID | None = None) -> Branding:
        """Marca del entrenador (por tenant). Sin caché: el selector de color de
        la UI la puede cambiar en vivo."""
        tenant = str(tenant_id) if tenant_id else "default"
        return load_branding(self.settings.branding_dir, tenant)


def build_container() -> Container:
    return Container()
