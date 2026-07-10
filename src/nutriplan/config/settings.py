"""Ajustes de entorno (Pydantic Settings + .env).

La estrategia nutricional NO vive aquí: está en config/nutrition.default.yaml
y se accede vía el puerto ConfigProvider. Aquí solo hay configuración de
infraestructura (DB, claves, modelos por tarea, logging).
"""

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    PROD = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Environment = Environment.LOCAL
    database_url: str = "sqlite+aiosqlite:///./data/app.db"
    anthropic_api_key: str = ""
    llm_model_ingest: str = "claude-haiku-4-5"
    llm_model_generate: str = "claude-sonnet-5"
    redis_url: str = ""
    object_storage_url: str = ""
    log_level: str = "INFO"
    session_secret: str = "dev-insecure-change-me"  # firma la cookie de sesión
    admin_email: str = ""  # si un signup usa este correo, la cuenta es admin

    # Rutas del proyecto (relativas a la raíz del repo)
    project_root: Path = Path(__file__).resolve().parents[3]

    @property
    def nutrition_config_path(self) -> Path:
        return self.project_root / "config" / "nutrition.default.yaml"

    @property
    def prompts_dir(self) -> Path:
        return self.project_root / "prompts"

    @property
    def branding_dir(self) -> Path:
        return self.project_root / "config" / "branding"

    @property
    def exports_dir(self) -> Path:
        return self.project_root / "data" / "exports"


@lru_cache
def get_settings() -> Settings:
    return Settings()
