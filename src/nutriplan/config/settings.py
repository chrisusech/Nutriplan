"""Ajustes de entorno (Pydantic Settings + .env).

La estrategia nutricional NO vive aquí: está en config/nutrition.default.yaml
y se accede vía el puerto ConfigProvider. Aquí solo hay configuración de
infraestructura (DB, claves, modelos por tarea, logging).
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    PROD = "prod"


# Firma la cookie de sesión Y los tokens de verificación y recuperación. Quien
# lo conozca puede forjar una sesión de super_user o un reset de cualquier
# correo, así que en prod arrancar con este valor es un fallo, no un aviso.
DEV_SESSION_SECRET = "dev-insecure-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Environment = Environment.LOCAL
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    run_migrations_on_start: bool = True
    anthropic_api_key: str = ""
    # Default de beta: Groq gpt-oss-20b (schema pequeño, barato).
    llm_model_generate: str = "openai/gpt-oss-20b"
    # Proveedor OpenAI-compatible (DeepSeek, Groq, OpenRouter, Ollama…).
    # Si ANTHROPIC_API_KEY está vacía y LLM_BASE_URL + LLM_API_KEY están seteadas,
    # se usa OpenAICompatClient en lugar del modo offline.
    llm_base_url: str = ""
    llm_api_key: str = ""
    # true + LLM_API_KEY → IA elige combos (flag de prueba). Producto: false —
    # el motor arma la semana; la IA solo escribe recetas al ver el día.
    llm_select_foods: bool = False
    # Soft: pasada crítica extra (otro call). En flujo lean va apagada: la
    # selección ya trae dish_name.
    llm_refine_names: bool = False
    # Si true, tras el menú se generan todas las recetas en background.
    # Lean default: false → una receta al abrir cada plato.
    llm_eager_recipes: bool = False
    # Reintentos de selección cuando manda la IA (cada uno = 1 call caro).
    # El motor offline sigue usando generation.max_retries del YAML.
    llm_select_max_retries: int = 1
    # Proveedor de reserva: vacío = solo el primario (flujo lean).
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""
    # Código de invitació para el alta pública. Vacío = abierto (local).
    # En prod de beta cerrada: un string compartido con los testers.
    beta_invite_code: str = ""
    # URL pública de la app: la necesitan los enlaces de correo.
    base_url: str = "http://127.0.0.1:8000"
    # Dominios que la app acepta servir. Vacío = sin comprobación (solo local);
    # sin esto, un Host falsificado envenena los enlaces de correo.
    allowed_hosts: str = ""

    # Identificadores de cliente OAuth. Sin ellos el login social queda apagado
    # (no falla el arranque: la app funciona con correo y contraseña).
    google_client_id: str = ""
    apple_client_id: str = ""

    # SMTP. Vacío = adaptador de consola: el correo se ve en el log.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "NutriPlan <no-reply@nutriplan.app>"

    log_level: str = "INFO"
    session_secret: str = DEV_SESSION_SECRET
    # Credenciales del super_user. Si ambas están seteadas y la cuenta no existe,
    # se siembra al arrancar. Es la única cuenta que no sale del alta pública.
    admin_email: str = ""
    admin_password: str = ""
    # El tick del domingo. En prod corre solo; en local hay que encenderlo.
    auto_week_tick: bool = False
    # POST /internal/tick. Vacío = la ruta no existe.
    internal_tick_secret: str = ""

    # Rutas del proyecto (relativas a la raíz del repo)
    project_root: Path = Path(__file__).resolve().parents[3]

    @model_validator(mode="after")
    def _prod_needs_a_real_secret(self) -> "Settings":
        if self.env is Environment.PROD and self.session_secret == DEV_SESSION_SECRET:
            raise ValueError(
                "SESSION_SECRET sigue en el valor de desarrollo. Genera uno: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return self

    @property
    def nutrition_config_path(self) -> Path:
        return self.project_root / "config" / "nutrition.default.yaml"

    @property
    def prompts_dir(self) -> Path:
        return self.project_root / "prompts"

    @property
    def food_classes_path(self) -> Path:
        return self.project_root / "data" / "meals" / "food_classes.yaml"

    @property
    def meal_templates_path(self) -> Path:
        return self.project_root / "data" / "meals" / "meal_templates.yaml"

    @property
    def recipes_catalog_path(self) -> Path:
        return self.project_root / "data" / "recipes" / "catalog.yaml"

    @property
    def restaurants_catalog_path(self) -> Path:
        return self.project_root / "data" / "restaurants" / "catalog.yaml"

    @property
    def branding_dir(self) -> Path:
        return self.project_root / "config" / "branding"


@lru_cache
def get_settings() -> Settings:
    return Settings()
