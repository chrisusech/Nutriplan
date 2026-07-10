"""Puerto de la capa de IA (sección 13.1).

Dos operaciones, ambas con Structured Outputs: el resultado SIEMPRE llega
como modelo Pydantic validado. La IA jamás devuelve texto libre al dominio.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMClient(Protocol):
    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        """Extracción estructurada (ingesta Word→JSON)."""
        ...

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        """Selección de alimentos para el plan (sin cantidades)."""
        ...

    def pop_usage(self) -> dict[str, int]:
        """Tokens acumulados desde la última lectura (observabilidad de costo)."""
        ...
