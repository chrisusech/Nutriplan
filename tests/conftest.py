from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from nutriplan.adapters.config_yaml import YamlConfigProvider
from nutriplan.config.settings import Settings, get_settings
from nutriplan.domain.nutrition_config import NutritionConfig

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Variables que apuntarían a un proveedor real. `Settings` lee el `.env` del
# desarrollador, así que basta con tener una clave puesta para que la suite
# empiece a gastar cuota de verdad — pasó, y por eso existe este guard.
_LLM_ENV = (
    "ANTHROPIC_API_KEY",
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "LLM_FALLBACK_API_KEY",
    "LLM_FALLBACK_BASE_URL",
)


@pytest.fixture(scope="session", autouse=True)
def _no_real_llm() -> Iterator[None]:
    """Ningún test puede hablar con un proveedor de IA.

    Dos candados, porque uno solo se escapa:

    1. Se vacían las variables de entorno y se desconecta el `.env`, para que
       `Settings` no herede la clave de nadie.
    2. Se vigila el transporte HTTP: si algún camino se saltara el primero, la
       llamada falla ruidosamente en vez de gastar cuota en silencio.
    """
    mp = pytest.MonkeyPatch()
    for name in _LLM_ENV:
        mp.delenv(name, raising=False)
    mp.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()

    real_send = httpx.AsyncClient.send

    async def guarded(self: httpx.AsyncClient, request: httpx.Request, **kw: Any) -> Any:
        host = request.url.host or ""
        if host and host not in ("testserver", "localhost", "127.0.0.1"):
            raise AssertionError(
                f"Un test intentó llamar a {host}. La suite no habla con "
                "proveedores de IA: usa MockLLMClient o el modo offline."
            )
        return await real_send(self, request, **kw)

    mp.setattr(httpx.AsyncClient, "send", guarded)
    yield
    mp.undo()
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def nutrition_config() -> NutritionConfig:
    provider = YamlConfigProvider(PROJECT_ROOT / "config" / "nutrition.default.yaml")
    return provider.get_nutrition_config()
