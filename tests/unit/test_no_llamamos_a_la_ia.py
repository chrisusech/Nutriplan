"""El candado que impide que la suite gaste cuota de IA.

Pasó de verdad: al poner la clave de Groq en el `.env`, catorce tests empezaron
a llamar al proveedor y la suite subió de 27 a 98 segundos. Nadie lo habría
notado hasta la factura.
"""

import httpx
import pytest

from nutriplan.config.settings import get_settings
from nutriplan.container import Container


def test_la_configuracion_de_los_tests_no_hereda_ninguna_clave() -> None:
    """Aunque el `.env` del desarrollador tenga una, aquí no llega."""
    s = get_settings()
    assert s.llm_api_key == ""
    assert s.llm_base_url == ""
    assert s.anthropic_api_key == ""


def test_sin_claves_el_container_arranca_en_modo_offline() -> None:
    """`None` = selector determinista. Es lo que usan todos los tests."""
    assert Container().llm_client is None


async def test_intentar_salir_a_internet_falla_ruidosamente() -> None:
    """El segundo candado: si alguien se salta la configuración, esto avisa."""
    async with httpx.AsyncClient() as client:
        with pytest.raises(AssertionError, match="no habla con"):
            await client.get("https://api.groq.com/openai/v1/models")
