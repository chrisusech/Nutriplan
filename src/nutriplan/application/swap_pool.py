"""Amplía el pool de un cambio de plato con lo que la nota pida del catálogo profundo.

El enum que ve la IA no puede llevar miles de ids: se busca primero, se restringe después.
"""

from uuid import UUID

import structlog

from nutriplan.domain.food_filter import allowed_foods
from nutriplan.domain.models import FoodItem
from nutriplan.domain.swap_note import food_needles
from nutriplan.ports.food_repository import FoodRepository

logger = structlog.get_logger(__name__)

# Tope del enum que lee el modelo. «pan» / «res» ya los resuelve el pool base.
MAX_RETRIEVED = 24
MIN_NEEDLE_LEN = 4


async def pool_for_note(
    *,
    note: str,
    base: list[FoodItem],
    food_repo: FoodRepository,
    restrictions: list[str],
    banned: set[UUID] | None = None,
) -> list[FoodItem]:
    """El pool base, más lo que la nota pida y esté en el catálogo profundo."""
    wish = note.strip()
    if not wish:
        return base

    known = {food.id for food in base}
    found: dict[UUID, FoodItem] = {}
    for needle in food_needles(wish):
        if len(needle) < MIN_NEEDLE_LEN or len(found) >= MAX_RETRIEVED:
            continue
        for food in await food_repo.search_deep(needle, limit=MAX_RETRIEVED):
            if food.id not in known:
                found.setdefault(food.id, food)

    if not found:
        return base

    usable = allowed_foods(list(found.values()), restrictions, banned or set())
    extra = usable[:MAX_RETRIEVED]
    if extra:
        logger.info(
            "swap_pool_ampliado",
            encontrados=len(found),
            aceptados=len(extra),
            nombres=[food.name_es for food in extra[:5]],
        )
    return base + extra


def promoted_ids(pool_extra: list[FoodItem], used_ids: set[UUID]) -> list[UUID]:
    """Ids del fondo que acabaron en el plato (se suman al perfil, no al catálogo)."""
    return [food.id for food in pool_extra if food.id in used_ids]
