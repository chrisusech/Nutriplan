"""Adaptador Anthropic del puerto LLMClient (sección 13).

- Structured Outputs vía messages.parse(output_format=<modelo Pydantic>):
  JSON garantizado por esquema, sin parseo frágil.
- Re-validación Pydantic al recibir (defensa en profundidad).
- Prompt caching en el system (parte estática) para bajar costo.
- Reintentos ante fallo de esquema; errores del proveedor → LLMError tipado.
- Registro de tokens por llamada (observabilidad de costo por plan).
"""

from typing import TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from nutriplan.domain.errors import LLMError

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

SCHEMA_RETRIES = 2  # reintentos si la respuesta no valida contra el esquema


class AnthropicClient:
    def __init__(self, api_key: str, *, max_tokens_extract: int = 2048,
                 max_tokens_select: int = 8192) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError(
                "Claude es opcional: instala el extra `anthropic` "
                "(uv sync --extra anthropic) o usa un proveedor OpenAI-compatible."
            ) from exc
        self._client = anthropic.AsyncAnthropic(api_key=api_key or None)
        self._anthropic = anthropic
        self._max_tokens_extract = max_tokens_extract
        self._max_tokens_select = max_tokens_select
        self._usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "calls": 0}

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        return await self._call(
            system=system, user=text, schema=schema, model=model,
            max_tokens=self._max_tokens_extract,
        )

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        return await self._call(
            system=system, user=prompt, schema=schema, model=model,
            max_tokens=self._max_tokens_select,
        )

    def pop_usage(self) -> dict[str, int]:
        usage, self._usage = self._usage, {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        return usage

    async def _call(
        self, *, system: str, user: str, schema: type[T], model: str, max_tokens: int
    ) -> T:
        last_error: Exception | None = None
        for attempt in range(1 + SCHEMA_RETRIES):
            try:
                response = await self._client.messages.parse(
                    model=model,
                    max_tokens=max_tokens,
                    system=[
                        {
                            "type": "text",
                            "text": system,
                            # parte estática cacheada → menos tokens por llamada
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                    messages=[{"role": "user", "content": user}],
                    output_format=schema,
                )
            except self._anthropic.APIConnectionError as exc:
                raise LLMError(f"Sin conexión con el proveedor de IA: {exc}") from exc
            except self._anthropic.APIStatusError as exc:
                raise LLMError(
                    f"Proveedor de IA devolvió {exc.status_code}: {exc.message}"
                ) from exc

            self._usage["calls"] += 1
            self._usage["input_tokens"] += response.usage.input_tokens
            self._usage["output_tokens"] += response.usage.output_tokens
            logger.info(
                "llm_call",
                model=model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_read=getattr(response.usage, "cache_read_input_tokens", 0),
                attempt=attempt,
            )

            parsed = response.parsed_output
            if parsed is None:  # la API respondió sin structured output utilizable
                last_error = LLMError("La respuesta no trae parsed_output")
                logger.warning("llm_no_parsed_output", attempt=attempt)
                continue
            try:
                # segunda barrera: re-validar aunque parse() ya validó
                return schema.model_validate(parsed.model_dump())
            except ValidationError as exc:
                last_error = exc
                logger.warning("llm_schema_invalid", attempt=attempt, error=str(exc))
                continue

        raise LLMError(
            f"Respuesta de IA no cumple el esquema {schema.__name__} "
            f"tras {1 + SCHEMA_RETRIES} intentos"
        ) from last_error
