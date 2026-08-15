"""La cadena de proveedores: qué pasa cuando el de turno no responde.

Un BETA sobre cuotas gratuitas se queda sin cuota. Lo que importa no es el
camino feliz, sino que quedarse sin cuota no deje a nadie sin menú.
"""

import pytest
from pydantic import BaseModel

from nutriplan.adapters.llm.fallback_client import FallbackLLMClient
from nutriplan.domain.errors import LLMError


class Respuesta(BaseModel):
    quien: str


class _Proveedor:
    """Cliente de mentira: responde con su nombre, o falla siempre."""

    def __init__(self, nombre: str, *, falla: bool = False) -> None:
        self.nombre = nombre
        self.falla = falla
        self.llamadas = 0

    async def extract(self, *, system: str, text: str, schema: type, model: str):  # type: ignore[no-untyped-def]
        return self._responder()

    async def select_plan(self, *, system: str, prompt: str, schema: type, model: str):  # type: ignore[no-untyped-def]
        return self._responder()

    def _responder(self) -> Respuesta:
        self.llamadas += 1
        if self.falla:
            raise LLMError(f"{self.nombre} sin cuota")
        return Respuesta(quien=self.nombre)

    def pop_usage(self) -> dict[str, int]:
        return {"input_tokens": 1, "output_tokens": 2, "calls": self.llamadas}


async def test_mientras_el_primero_responda_el_de_reserva_ni_se_entera() -> None:
    groq, openrouter = _Proveedor("groq"), _Proveedor("openrouter")
    chain = FallbackLLMClient([("groq", groq), ("openrouter", openrouter)])

    result = await chain.select_plan(system="s", prompt="p", schema=Respuesta, model="m")

    assert result.quien == "groq"
    assert chain.last_provider == "groq"
    assert openrouter.llamadas == 0


async def test_si_el_primero_se_queda_sin_cuota_entra_el_siguiente() -> None:
    groq = _Proveedor("groq", falla=True)
    openrouter = _Proveedor("openrouter")
    chain = FallbackLLMClient([("groq", groq), ("openrouter", openrouter)])

    result = await chain.select_plan(system="s", prompt="p", schema=Respuesta, model="m")

    assert result.quien == "openrouter"
    assert chain.last_provider == "openrouter"


async def test_si_ninguno_responde_falla_diciendo_que_paso_con_cada_uno() -> None:
    """El error tiene que nombrar a los dos: si no, no se sabe qué arreglar."""
    chain = FallbackLLMClient(
        [
            ("groq", _Proveedor("groq", falla=True)),
            ("openrouter", _Proveedor("openrouter", falla=True)),
        ]
    )
    with pytest.raises(LLMError) as exc:
        await chain.select_plan(system="s", prompt="p", schema=Respuesta, model="m")
    assert "groq" in str(exc.value)
    assert "openrouter" in str(exc.value)


async def test_el_fallback_tambien_cubre_la_extraccion() -> None:
    chain = FallbackLLMClient(
        [
            ("groq", _Proveedor("groq", falla=True)),
            ("openrouter", _Proveedor("openrouter")),
        ]
    )
    result = await chain.extract(system="s", text="t", schema=Respuesta, model="m")
    assert result.quien == "openrouter"


async def test_el_consumo_se_suma_entre_todos_los_proveedores() -> None:
    groq = _Proveedor("groq", falla=True)
    openrouter = _Proveedor("openrouter")
    chain = FallbackLLMClient([("groq", groq), ("openrouter", openrouter)])
    await chain.select_plan(system="s", prompt="p", schema=Respuesta, model="m")

    usage = chain.pop_usage()
    assert usage["calls"] == 2  # el que falló también gastó
    assert usage["input_tokens"] == 2


def test_una_cadena_vacia_es_un_error_de_configuracion_no_un_modo() -> None:
    """Sin proveedores el container devuelve None (modo offline), no una cadena vacía."""
    with pytest.raises(ValueError):
        FallbackLLMClient([])
