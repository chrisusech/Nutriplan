"""Cadena de proveedores: si el primero falla, entra el siguiente.

Un BETA público sobre cuotas gratuitas se queda sin cuota. Cuando eso pasa, la
alternativa no es "no hay menú": es el proveedor de reserva, y si tampoco está,
el selector determinista que ya vive en el repo. La app nunca depende de que un
tercero esté de buenas.
"""

from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog
from pydantic import BaseModel

from nutriplan.domain.errors import LLMError
from nutriplan.ports.llm_client import LLMClient

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class FallbackLLMClient:
    """Prueba cada cliente en orden y devuelve la primera respuesta buena."""

    def __init__(self, clients: list[tuple[str, LLMClient]]) -> None:
        if not clients:
            raise ValueError("La cadena de proveedores no puede estar vacía")
        self._clients = clients
        # Con qué proveedor se resolvió la última llamada: va a la procedencia
        # del plan, así que "esto lo eligió Groq" es una respuesta comprobable.
        self.last_provider: str | None = None

    @property
    def providers(self) -> list[str]:
        return [name for name, _ in self._clients]

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        return await self._try_each(
            "extract", lambda c: c.extract(system=system, text=text, schema=schema, model=model)
        )

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        return await self._try_each(
            "select_plan",
            lambda c: c.select_plan(system=system, prompt=prompt, schema=schema, model=model),
        )

    def pop_usage(self) -> dict[str, int]:
        total = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
        for _, client in self._clients:
            for key, value in client.pop_usage().items():
                total[key] = total.get(key, 0) + value
        return total

    async def _try_each(self, operation: str, call: Callable[[LLMClient], Awaitable[T]]) -> T:
        errors: list[str] = []
        for index, (name, client) in enumerate(self._clients):
            try:
                result = await call(client)
            except LLMError as exc:
                errors.append(f"{name}: {exc}")
                logger.warning(
                    "llm_provider_failed", provider=name, operation=operation, error=str(exc)
                )
                continue
            self.last_provider = name
            if index > 0:
                logger.info("llm_fallback_used", provider=name, after=index)
            return result
        raise LLMError("Ningún proveedor de IA respondió. " + " | ".join(errors))
