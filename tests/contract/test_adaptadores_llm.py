"""Los adaptadores de IA cumplen el puerto, contra respuestas grabadas.

Ninguno sale a la red: el proveedor está sustituido por un doble que devuelve
lo que devolvería de verdad. Lo que se comprueba es el contrato —qué promete el
adaptador a quien lo usa— no el proveedor.
"""

import sys
import types

import pytest
from pydantic import BaseModel

from nutriplan.domain.errors import LLMError


class Respuesta(BaseModel):
    elegidos: list[str]


class _Uso:
    input_tokens = 120
    output_tokens = 40
    cache_read_input_tokens = 90


class _Parsed:
    def __init__(self, valor: Respuesta | None) -> None:
        self.parsed_output = valor
        self.usage = _Uso()


def _anthropic_falso(monkeypatch, *, respuestas: list, errores=None) -> types.ModuleType:
    """Un módulo `anthropic` de mentira, con la forma que usa el adaptador."""
    mod = types.ModuleType("anthropic")

    class APIConnectionError(Exception):
        pass

    class APIStatusError(Exception):
        def __init__(self, status_code: int, message: str) -> None:
            super().__init__(message)
            self.status_code = status_code
            self.message = message

    class _Messages:
        def __init__(self) -> None:
            self.llamadas = 0

        async def parse(self, **_kwargs):
            self.llamadas += 1
            if errores:
                raise errores.pop(0)
            return respuestas.pop(0)

    class AsyncAnthropic:
        def __init__(self, api_key=None) -> None:
            self.messages = _Messages()

    mod.APIConnectionError = APIConnectionError
    mod.APIStatusError = APIStatusError
    mod.AsyncAnthropic = AsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", mod)
    return mod


def _cliente(monkeypatch, **kwargs):
    from nutriplan.adapters.llm.anthropic_client import AnthropicClient

    _anthropic_falso(monkeypatch, **kwargs)
    return AnthropicClient(api_key="k")


async def test_una_respuesta_valida_llega_tipada_a_quien_la_pidio(monkeypatch) -> None:
    cliente = _cliente(monkeypatch, respuestas=[_Parsed(Respuesta(elegidos=["a", "b"]))])
    salida = await cliente.extract(
        system="s", text="t", schema=Respuesta, model="claude-x"
    )
    assert salida.elegidos == ["a", "b"]


async def test_se_lleva_la_cuenta_de_lo_que_costo(monkeypatch) -> None:
    """Sin esto no hay forma de saber cuánto cuesta un menú."""
    cliente = _cliente(monkeypatch, respuestas=[_Parsed(Respuesta(elegidos=["a"]))])
    await cliente.extract(system="s", text="t", schema=Respuesta, model="claude-x")

    uso = cliente.pop_usage()
    assert uso == {"input_tokens": 120, "output_tokens": 40, "calls": 1}
    # Y se vacía: la siguiente lectura es de la siguiente tanda, no acumulada.
    assert cliente.pop_usage()["calls"] == 0


async def test_si_la_respuesta_no_trae_nada_utilizable_se_reintenta(monkeypatch) -> None:
    cliente = _cliente(
        monkeypatch,
        respuestas=[_Parsed(None), _Parsed(Respuesta(elegidos=["c"]))],
    )
    salida = await cliente.select_plan(
        system="s", prompt="p", schema=Respuesta, model="claude-x"
    )
    assert salida.elegidos == ["c"]


async def test_si_nunca_cumple_el_esquema_se_rinde_con_un_error_del_dominio(
    monkeypatch,
) -> None:
    """El resto de la app solo sabe de `LLMError`; el proveedor no se filtra."""
    cliente = _cliente(monkeypatch, respuestas=[_Parsed(None)] * 3)
    with pytest.raises(LLMError, match="no cumple el esquema"):
        await cliente.extract(system="s", text="t", schema=Respuesta, model="claude-x")


async def test_quedarse_sin_conexion_es_un_error_del_dominio(monkeypatch) -> None:
    from nutriplan.adapters.llm.anthropic_client import AnthropicClient

    mod = _anthropic_falso(monkeypatch, respuestas=[])
    cliente = AnthropicClient(api_key="k")
    cliente._client.messages.parse = _lanza(mod.APIConnectionError("cable suelto"))

    with pytest.raises(LLMError, match="Sin conexión"):
        await cliente.extract(system="s", text="t", schema=Respuesta, model="claude-x")


async def test_un_rechazo_del_proveedor_dice_el_codigo(monkeypatch) -> None:
    """Un 429 y un 500 se arreglan distinto: el código tiene que llegar."""
    from nutriplan.adapters.llm.anthropic_client import AnthropicClient

    mod = _anthropic_falso(monkeypatch, respuestas=[])
    cliente = AnthropicClient(api_key="k")
    cliente._client.messages.parse = _lanza(mod.APIStatusError(429, "demasiadas"))

    with pytest.raises(LLMError, match="429"):
        await cliente.extract(system="s", text="t", schema=Respuesta, model="claude-x")


def _lanza(error: Exception):
    async def _parse(**_kwargs):
        raise error

    return _parse


def test_sin_el_extra_instalado_se_explica_como_instalarlo(monkeypatch) -> None:
    """`anthropic` es opcional. Quien no lo tenga merece una frase útil, no un
    ImportError pelado."""
    import builtins

    from nutriplan.adapters.llm.anthropic_client import AnthropicClient

    real = builtins.__import__

    def _sin_anthropic(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "anthropic", raising=False)
    monkeypatch.setattr(builtins, "__import__", _sin_anthropic)

    with pytest.raises(LLMError, match="uv sync --extra anthropic"):
        AnthropicClient(api_key="k")
