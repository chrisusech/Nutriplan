"""El adaptador OpenAI-compatible, sin red.

Lo que importa: que el JSON Schema que se le manda a Groq sea el que su modo
estricto acepta, y que una respuesta mala no se cuele como plan.
"""

import json

import httpx
import pytest
from pydantic import BaseModel, Field

from nutriplan.adapters.llm.openai_compat_client import (
    OpenAICompatClient,
    _strict_schema,
    supports_strict_schema,
)
from nutriplan.domain.errors import LLMError


class Plato(BaseModel):
    nombre: str
    pasos: list[str] = Field(default_factory=list)  # con default: Pydantic no lo marca requerido


def _responde(monkeypatch, *payloads, status: int = 200) -> list[dict]:
    """Sustituye la red por respuestas grabadas. Devuelve lo que se envió."""
    enviados: list[dict] = []
    cola = list(payloads)

    class _Fake:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, headers=None, json=None):  # noqa: A002
            enviados.append(json)
            return httpx.Response(
                status,
                json=cola.pop(0) if cola else {},
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr(
        "nutriplan.adapters.llm.openai_compat_client.httpx.AsyncClient", lambda **kw: _Fake()
    )
    return enviados


def _ok(content: dict) -> dict:
    return {
        "choices": [{"message": {"content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


# --- El schema estricto -----------------------------------------------------


def test_los_modelos_de_pesos_abiertos_de_groq_van_en_modo_estricto() -> None:
    assert supports_strict_schema("openai/gpt-oss-120b")
    assert supports_strict_schema("openai/gpt-oss-20b")
    assert not supports_strict_schema("llama-3.3-70b-versatile")
    assert not supports_strict_schema("deepseek-chat")


def test_el_modo_estricto_exige_que_toda_propiedad_sea_requerida() -> None:
    """Pydantic omite de `required` lo que tiene default; Groq rechaza el
    esquema entero si falta alguna. Sin esto, el modo estricto no arranca."""
    schema = _strict_schema(Plato)["json_schema"]["schema"]

    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_el_modo_estricto_se_declara_con_nombre_y_strict() -> None:
    fmt = _strict_schema(Plato)
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["name"] == "Plato"
    assert fmt["json_schema"]["strict"] is True


# --- Lo que se manda por el cable -------------------------------------------


async def test_con_gpt_oss_se_manda_el_schema_y_no_se_ensucia_el_system(monkeypatch) -> None:
    enviados = _responde(monkeypatch, _ok({"nombre": "Arepa", "pasos": ["Asa"]}))
    client = OpenAICompatClient("k", base_url="https://api.groq.com/openai/v1")

    await client.extract(system="Eres X", text="t", schema=Plato, model="openai/gpt-oss-120b")

    cuerpo = enviados[0]
    assert cuerpo["response_format"]["type"] == "json_schema"
    # En modo estricto el esquema no hace falta repetirlo en el prompt
    assert cuerpo["messages"][0]["content"] == "Eres X"


async def test_con_un_modelo_sin_modo_estricto_el_schema_va_en_el_prompt(monkeypatch) -> None:
    enviados = _responde(monkeypatch, _ok({"nombre": "Arepa", "pasos": []}))
    client = OpenAICompatClient("k")

    await client.extract(system="Eres X", text="t", schema=Plato, model="deepseek-chat")

    cuerpo = enviados[0]
    assert cuerpo["response_format"] == {"type": "json_object"}
    assert "Plato" in cuerpo["messages"][0]["content"]


async def test_se_mandan_los_dos_nombres_del_tope_de_tokens(monkeypatch) -> None:
    """Unos proveedores solo aceptan el nuevo; los viejos ignoran el que no conocen."""
    enviados = _responde(monkeypatch, _ok({"nombre": "x", "pasos": []}))
    await OpenAICompatClient("k").extract(system="s", text="t", schema=Plato, model="m")

    assert enviados[0]["max_tokens"] == enviados[0]["max_completion_tokens"]


# --- Cuando la respuesta viene mal ------------------------------------------


async def test_una_respuesta_que_no_cumple_el_esquema_se_reintenta_y_luego_falla(
    monkeypatch,
) -> None:
    _responde(monkeypatch, _ok({"otra_cosa": 1}), _ok({"otra_cosa": 2}), _ok({"otra_cosa": 3}))
    with pytest.raises(LLMError, match="no cumple el esquema"):
        await OpenAICompatClient("k").extract(system="s", text="t", schema=Plato, model="m")


async def test_un_reintento_que_sale_bien_devuelve_el_resultado(monkeypatch) -> None:
    _responde(monkeypatch, _ok({"mal": 1}), _ok({"nombre": "Arepa", "pasos": ["Asa"]}))
    out = await OpenAICompatClient("k").extract(system="s", text="t", schema=Plato, model="m")
    assert out.nombre == "Arepa"


async def test_una_respuesta_vacia_no_se_toma_por_buena(monkeypatch) -> None:
    vacia = {"choices": [{"message": {"content": ""}}]}
    _responde(monkeypatch, vacia, vacia, vacia)
    with pytest.raises(LLMError):
        await OpenAICompatClient("k").extract(system="s", text="t", schema=Plato, model="m")


async def test_si_no_hay_red_es_un_LLMError_tipado_y_no_una_excepcion_de_httpx(
    monkeypatch,
) -> None:
    """El fallback solo captura LLMError: una httpx.HTTPError se saltaría la cadena."""

    class _Roto:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **kw):
            raise httpx.ConnectError("sin red")

    monkeypatch.setattr(
        "nutriplan.adapters.llm.openai_compat_client.httpx.AsyncClient", lambda **kw: _Roto()
    )
    with pytest.raises(LLMError, match="Sin conexión"):
        await OpenAICompatClient("k").extract(system="s", text="t", schema=Plato, model="m")


async def test_el_consumo_de_tokens_queda_registrado(monkeypatch) -> None:
    _responde(monkeypatch, _ok({"nombre": "x", "pasos": []}))
    client = OpenAICompatClient("k")
    await client.extract(system="s", text="t", schema=Plato, model="m")

    usage = client.pop_usage()
    assert usage == {"input_tokens": 10, "output_tokens": 5, "calls": 1}
    assert client.pop_usage()["calls"] == 0, "leer el consumo lo reinicia"
