"""Mock del puerto LLMClient — respuestas grabadas, cero llamadas reales.

Se usa en tests (CI nunca toca la API) y como modo offline de la UI cuando
no hay ANTHROPIC_API_KEY. Valida contra el esquema igual que el real: un
fixture inválido debe fallar como fallaría en producción.
"""

from collections import deque
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from nutriplan.domain.errors import LLMError

T = TypeVar("T", bound=BaseModel)


class MockLLMClient:
    def __init__(self) -> None:
        self._responses: deque[dict[str, Any]] = deque()
        self.calls: list[dict[str, Any]] = []  # inspección en tests (system/prompt/model/schema)

    def enqueue(self, payload: dict[str, Any]) -> None:
        """Agrega una respuesta grabada (dict JSON) a la cola FIFO."""
        self._responses.append(payload)

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        return self._next(schema, kind="extract", system=system, user=text, model=model)

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        return self._next(schema, kind="select_plan", system=system, user=prompt, model=model)

    def pop_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "calls": len(self.calls)}

    def _next(self, schema: type[T], **call_info: str) -> T:
        self.calls.append({**call_info, "schema": schema.__name__})
        if not self._responses:
            raise LLMError("MockLLMClient sin respuestas encoladas")
        payload = self._responses.popleft()
        try:
            return schema.model_validate(payload)
        except ValidationError as exc:
            raise LLMError(
                f"Respuesta grabada no cumple el esquema {schema.__name__}: {exc}"
            ) from exc
