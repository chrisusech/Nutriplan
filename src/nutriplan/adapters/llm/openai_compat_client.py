"""Adaptador OpenAI-compatible del puerto LLMClient.

Funciona con Groq, OpenRouter, DeepSeek, Mistral, Gemini (shim OpenAI) y Ollama
local. Pide `json_schema` estricto cuando el proveedor lo soporta —Groq lo
garantiza en los modelos `gpt-oss`, que es la razón de elegirlos— y cae a
`json_object` cuando no. En ambos casos Pydantic valida en el borde: el schema
del proveedor acelera, no sustituye.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, TypeVar

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from nutriplan.domain.errors import LLMError

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

SCHEMA_RETRIES = 2
# Un nivel gratuito se topa con su cuota por minuto constantemente; el
# proveedor dice cuánto falta y esperar es más barato que fallar.
RATE_LIMIT_RETRIES = 2
MAX_RATE_WAIT_S = 45.0
DEFAULT_TIMEOUT = 120.0

# Ojo con `max_tokens`: los proveedores lo cuentan contra la cuota por minuto
# ANTES de generar nada, así que pedir de más rechaza la petición entera. El
# nivel gratis de Groq da 8.000 TPM.
#
# Y ojo doble con los modelos de razonamiento: los tokens que gastan pensando
# salen del MISMO presupuesto. Medido contra Groq con gpt-oss-120b, una semana
# completa consume ~3.270 tokens de salida entre razonamiento y respuesta, así
# que 3.072 truncaba SIEMPRE —devolvía tres días de siete— y el esquema lo
# rechazaba. 4.096 deja margen y sigue cabiendo en los 8.000 TPM junto con el
# prompt (~2.600). Bajar el esfuerzo de razonamiento no es la salida: entrega
# los siete días pero incumple otras restricciones del esquema.

# Cuánto se le concede a la IA antes de tirar del motor determinista. Nadie va
# a mirar una rueda dos minutos porque un proveedor gratuito esté saturado.
DEFAULT_BUDGET_S = 45.0

# Modelos que garantizan decodificación restringida contra el JSON Schema.
STRICT_SCHEMA_MODELS = ("gpt-oss", "gpt-4o", "gpt-4.1")


def supports_strict_schema(model: str) -> bool:
    return any(marker in model for marker in STRICT_SCHEMA_MODELS)


def _strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """El JSON Schema como lo quiere el modo estricto de OpenAI/Groq.

    Exige `additionalProperties: false` y que TODA propiedad esté en `required`;
    Pydantic omite de `required` las que tienen default, así que hay que
    completarlas o el proveedor rechaza el esquema entero.
    """
    root = schema.model_json_schema()

    def tighten(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for value in node.values():
                tighten(value)
        elif isinstance(node, list):
            for item in node:
                tighten(item)

    tighten(root)
    return {
        "type": "json_schema",
        "json_schema": {"name": schema.__name__, "strict": True, "schema": root},
    }


class OpenAICompatClient:
    """Puerto LLMClient vía chat/completions (formato OpenAI)."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.openai.com/v1",
        max_tokens_extract: int = 4096,
        max_tokens_select: int = 4096,
        budget_s: float = DEFAULT_BUDGET_S,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._max_tokens_extract = max_tokens_extract
        self._max_tokens_select = max_tokens_select
        self._budget_s = budget_s
        self._timeout = timeout
        self._usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        return await self._call(
            system=system,
            user=text,
            schema=schema,
            model=model,
            max_tokens=self._max_tokens_extract,
        )

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        return await self._call(
            system=system,
            user=prompt,
            schema=schema,
            model=model,
            max_tokens=self._max_tokens_select,
            temperature=0.55,
        )

    def pop_usage(self) -> dict[str, int]:
        usage, self._usage = self._usage, {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        return usage

    async def _call(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        model: str,
        max_tokens: int,
        temperature: float = 0.2,
    ) -> T:
        strict = supports_strict_schema(model)
        if strict:
            response_format = _strict_schema(schema)
            augmented_system = system
        else:
            schema_json = json.dumps(schema.model_json_schema(), ensure_ascii=False)
            response_format = {"type": "json_object"}
            augmented_system = (
                f"{system}\n\nResponde ÚNICAMENTE con JSON válido que cumpla este esquema "
                f"({schema.__name__}):\n{schema_json}"
            )
        last_error: Exception | None = None
        rate_waits = 0
        deadline = time.monotonic() + self._budget_s

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for attempt in range(1 + SCHEMA_RETRIES + RATE_LIMIT_RETRIES):
                if time.monotonic() > deadline:
                    logger.warning("llm_budget_spent", attempt=attempt)
                    break
                try:
                    body: dict[str, Any] = {
                        "model": model,
                        "temperature": temperature,
                        "response_format": response_format,
                        "messages": [
                            {"role": "system", "content": augmented_system},
                            {"role": "user", "content": user},
                        ],
                    }
                    # Gemini OpenAI-compat rechaza si van los dos a la vez.
                    # Groq/OpenAI aceptan `max_completion_tokens`.
                    if "generativelanguage.googleapis.com" in self._base_url:
                        body["max_tokens"] = max_tokens
                    else:
                        body["max_completion_tokens"] = max_tokens
                        body["max_tokens"] = max_tokens
                    response = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self._api_key}",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    )
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    wait = _retry_after(exc.response)
                    if (
                        wait is not None
                        and rate_waits < RATE_LIMIT_RETRIES
                        and time.monotonic() + wait < deadline
                    ):
                        rate_waits += 1
                        logger.info(
                            "llm_transient_retry",
                            status=exc.response.status_code,
                            wait_s=round(wait, 1),
                        )
                        await asyncio.sleep(wait)
                        continue
                    # El cuerpo trae el motivo real ("schema too complex",
                    # "rate limit", "unsupported format"). Sin él, depurar un
                    # 400 es adivinar.
                    detail = exc.response.text[:300]
                    raise LLMError(
                        f"El proveedor de IA rechazó la petición "
                        f"({exc.response.status_code}): {detail}"
                    ) from exc
                except httpx.HTTPError as exc:
                    raise LLMError(f"Sin conexión con el proveedor de IA: {exc}") from exc

                payload = response.json()
                self._record_usage(payload)

                content = _message_content(payload)
                if not content:
                    last_error = LLMError("Respuesta vacía del proveedor")
                    logger.warning("llm_empty_content", attempt=attempt)
                    continue

                try:
                    data: Any = json.loads(content)
                    return schema.model_validate(data)
                except (json.JSONDecodeError, ValidationError) as exc:
                    last_error = exc
                    logger.warning("llm_schema_invalid", attempt=attempt, error=str(exc))
                    continue

        raise LLMError(
            f"Respuesta de IA no cumple el esquema {schema.__name__} "
            f"tras {1 + SCHEMA_RETRIES} intentos"
        ) from last_error

    def _record_usage(self, payload: dict[str, Any]) -> None:
        usage = payload.get("usage") or {}
        self._usage["calls"] += 1
        self._usage["input_tokens"] += int(usage.get("prompt_tokens") or 0)
        self._usage["output_tokens"] += int(usage.get("completion_tokens") or 0)
        logger.info(
            "llm_call",
            provider="openai_compat",
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


def _message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _retry_after(response: httpx.Response) -> float | None:
    """Segundos a esperar ante 429/503, o None si no vale reintentar."""
    if response.status_code not in (429, 503):
        return None
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(float(header), MAX_RATE_WAIT_S)
        except ValueError:
            pass
    # Groq a veces mete el tiempo en el cuerpo.
    match = re.search(r"try again in ([\d.]+)s", response.text)
    if match:
        return min(float(match.group(1)) + 1.0, MAX_RATE_WAIT_S)
    # 503 de Gemini ("high demand") suele ser corto; 429 sin pista, un poco más.
    return 3.0 if response.status_code == 503 else 8.0
