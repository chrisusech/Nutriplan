"""Fija el modelo de un cliente LLM: el fallback no tiene por qué usar el mismo.

Groq y Gemini Free (u OpenRouter) no comparten nombres de modelo. El call site
pasa `LLM_MODEL_GENERATE` pensado para el primario; el de reserva necesita el
suyo (`LLM_FALLBACK_MODEL`) o la petición rebota antes de generar nada.
"""

from typing import TypeVar

from pydantic import BaseModel

from nutriplan.ports.llm_client import LLMClient

T = TypeVar("T", bound=BaseModel)


class ModelPinnedClient:
    """Delega al cliente interno sustituyendo siempre el `model` del call site."""

    def __init__(self, inner: LLMClient, model: str) -> None:
        if not model.strip():
            raise ValueError("ModelPinnedClient necesita un modelo no vacío")
        self._inner = inner
        self._model = model.strip()

    @property
    def model(self) -> str:
        return self._model

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        return await self._inner.extract(system=system, text=text, schema=schema, model=self._model)

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        return await self._inner.select_plan(
            system=system, prompt=prompt, schema=schema, model=self._model
        )

    def pop_usage(self) -> dict[str, int]:
        return self._inner.pop_usage()
