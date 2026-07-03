"""Puerto de la base de alimentos."""

from typing import Protocol
from uuid import UUID

from nutriplan.domain.models import FoodCategory, FoodItem


class FoodRepository(Protocol):
    async def upsert_globals(self, foods: list[FoodItem]) -> None:
        """Carga/actualiza alimentos globales (tenant_id NULL). Idempotente."""
        ...

    async def add_custom(self, food: FoodItem) -> None:
        """Alimento custom del tenant actual."""
        ...

    async def get_by_ids(self, food_ids: list[UUID]) -> list[FoodItem]: ...

    async def list_universe(self) -> list[FoodItem]:
        """Globales + custom del tenant actual (universo consultable)."""
        ...

    async def search(self, query: str, category: FoodCategory | None = None) -> list[FoodItem]: ...
