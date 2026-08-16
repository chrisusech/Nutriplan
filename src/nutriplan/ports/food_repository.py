"""Puerto de la base de alimentos.

El catálogo tiene dos niveles y el puerto los distingue a propósito: lo que se
LISTA (`list_universe`, `search`) son los pocos cientos que una persona
reconoce; lo que está VIVO (`search_deep`) son los miles de alimentos curados de
USDA, que no se listan nunca y solo se alcanzan buscándolos por nombre. Sin esa
separación, el picker del perfil y el enum que ve la IA se vuelven inmanejables.
"""

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

    async def update(self, food: FoodItem, *, edited_by: str | None = None) -> None:
        """Corrige un alimento existente. La base es la fuente de verdad."""
        ...

    async def retire(self, food_id: UUID) -> None:
        """Lo saca del catálogo sin borrarlo: los platos viejos lo siguen nombrando."""
        ...

    async def get_by_ids(self, food_ids: list[UUID]) -> list[FoodItem]: ...

    async def list_universe(self) -> list[FoodItem]:
        """Lo que se le enseña a la persona: listables globales + custom del tenant."""
        ...

    async def search(self, query: str, category: FoodCategory | None = None) -> list[FoodItem]:
        """Busca dentro de lo listable."""
        ...

    async def search_deep(
        self, query: str, category: FoodCategory | None = None, limit: int = 30
    ) -> list[FoodItem]:
        """Busca en todo el catálogo vivo. Acotado: el resultado acaba en un enum."""
        ...

    async def list_for_console(
        self,
        *,
        query: str = "",
        category: FoodCategory | None = None,
        only_listable: bool | None = None,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[FoodItem], int]:
        """Paginado para la consola de alimentos. Devuelve (página, total)."""
        ...
