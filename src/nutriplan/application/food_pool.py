"""El conjunto de alimentos con el que se le puede armar el menú a alguien.

Vivía copiado en cuatro sitios (generar, editar y dos pantallas del generador),
lo que hizo que "sorpréndeme" funcionara en uno y fallara en los otros tres.
"""

from collections.abc import Callable, Sequence
from uuid import UUID

import structlog

from nutriplan.domain.food_filter import allowed_foods
from nutriplan.domain.generation_rules import (
    CARB_GROUP,
    FAT_GROUP,
    PROTEIN_GROUP,
    SLOT_STRUCTURE,
)
from nutriplan.domain.models import (
    Client,
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealSlot,
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import usable_in_slot
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.repository import ClientRepository

logger = structlog.get_logger(__name__)

# Piso por slot: carbo y grasa. Proteína no se inyecta si el liked ya trae carne.
# Lácteo/fruta solo si el grupo P o C quedó en cero.
_MIN_SLOT_OPTIONS = 2

# No usar dulces como “parche” de carbo: cuadran macros pero no son desayuno.
_CARB_SUPPLEMENT_SKIP = (
    "miel",
    "azúcar",
    "azucar",
    "mermelada",
    "chocolate",
    "dulce",
    "galleta",
    "galletas",
)


def matches_dislike(food: FoodItem, disliked: set[str]) -> bool:
    """Un dislike es texto libre: casa contra el nombre y contra los alias."""
    haystack = {food.name_es.lower(), *(a.lower() for a in food.aliases)}
    return any(d in h or h in d for d in disliked for h in haystack)


async def resolve_allowed_foods(
    client: Client, *, food_repo: FoodRepository, client_repo: ClientRepository
) -> list[FoodItem]:
    """Preferencias ∩ no-restringidos ∩ no-vetados ∩ no-rechazados.

    Sin alimentos marcados la respuesta no es "nada", es "sorpréndeme": se abre
    el catálogo entero y lo recortan las restricciones. Obligar a marcar decenas
    de ingredientes era justo lo que agobiaba en el onboarding viejo.

    Los condimentos (ajo, etc.) no se marcan en el picker y aun así entran al
    motor: no son una elección, son la cocina. Quien los vete en /mis-alimentos
    sí los saca.
    """
    liked = (
        await food_repo.get_by_ids(client.liked_food_ids)
        if client.liked_food_ids
        else await food_repo.list_universe()
    )
    disliked = {d.strip().lower() for d in client.dislikes if d.strip()}
    if disliked:
        liked = [f for f in liked if not matches_dislike(f, disliked)]
    banned: set[UUID] = set(await client_repo.list_banned_food_ids(client.id))
    allowed = allowed_foods(liked, client.restrictions, banned)
    if client.liked_food_ids:
        universe = await food_repo.list_universe()
        have = {f.id for f in allowed}
        staples = [f for f in universe if "condimento" in f.tags and f.id not in have]
        if staples:
            allowed = [*allowed, *allowed_foods(staples, client.restrictions, banned)]
    return allowed


def _count(
    pool: Sequence[FoodItem],
    slot: MealSlot,
    categories: set[FoodCategory],
    usable: Callable[[FoodItem, MealSlot], bool],
) -> int:
    return sum(
        1
        for food in pool
        if food.category in categories and slot in food.meal_slots and usable(food, slot)
    )


def _density(food: FoodItem, category: FoodCategory) -> float:
    if category is FoodCategory.CARB:
        return food.carb_100g
    if category is FoodCategory.FAT:
        return food.fat_100g
    return food.protein_100g


def supplement_pool_for_targets(
    allowed: list[FoodItem],
    universe: list[FoodItem],
    *,
    daily: MacroTargets,
    config: NutritionConfig,
    disliked: set[str] | None = None,
    banned: set[UUID] | None = None,
    restrictions: Sequence[str] | None = None,
) -> list[FoodItem]:
    """Si el pool liked no da para el slot, añade del universo lo mínimo.

    Caso típico: desayuno pide 128 g de carbo y solo hay arepa/pan (tope ~64 g).
    Sin avena u otro carbo en gramos el solver nunca cuadra. Preferencias siguen
    mandando; esto solo evita un menú imposible — o repetido hasta el aburrimiento,
    que es lo que sale cuando el slot tiene una única opción viable.

    Restricciones recortan el universo *antes* de elegir el parche: con
    `no_gluten` no entra pasta aunque falte carbo.
    """
    disliked = disliked or set()
    banned = banned or set()
    usable = usable_in_slot(daily, config)
    pool = list(allowed)
    have = {f.id for f in pool}
    extra: list[FoodItem] = []
    try:
        universe_ok = allowed_foods(universe, list(restrictions or []), banned)
    except ValueError:
        universe_ok = [f for f in universe if f.id not in banned]

    def add(slot: MealSlot, category: FoodCategory, faltan: int) -> None:
        if faltan <= 0:
            return
        skip = _CARB_SUPPLEMENT_SKIP if category is FoodCategory.CARB else ()
        candidates = sorted(
            (
                food
                for food in universe_ok
                if food.id not in have
                and food.category is category
                and slot in food.meal_slots
                and not matches_dislike(food, disliked)
                and usable(food, slot)
                and not any(s in food.name_es.lower() for s in skip)
            ),
            key=lambda food: (
                0 if food.unit_granularity is UnitGranularity.GRAMS else 1,
                -_density(food, category),
                food.name_es,
            ),
        )
        for pick in candidates[:faltan]:
            extra.append(pick)
            pool.append(pick)
            have.add(pick.id)
            logger.info(
                "pool_supplemented",
                slot=slot.value,
                category=category.value,
                food=pick.name_es,
            )

    for slot in config.meal_distribution:
        rule = SLOT_STRUCTURE[slot]
        if rule.requires_carb:
            add(
                slot,
                FoodCategory.CARB,
                _MIN_SLOT_OPTIONS - _count(pool, slot, {FoodCategory.CARB}, usable),
            )
        if _count(pool, slot, CARB_GROUP, usable) == 0:
            add(slot, FoodCategory.FRUIT, _MIN_SLOT_OPTIONS)
        if not rule.requires_carb and _count(pool, slot, {FoodCategory.FRUIT}, usable) == 0:
            add(slot, FoodCategory.FRUIT, _MIN_SLOT_OPTIONS)
            if _count(pool, slot, {FoodCategory.FRUIT, FoodCategory.DAIRY}, usable) == 0:
                add(slot, FoodCategory.DAIRY, _MIN_SLOT_OPTIONS)
        if rule.allows_fat_item:
            add(
                slot,
                FoodCategory.FAT,
                _MIN_SLOT_OPTIONS - _count(pool, slot, FAT_GROUP, usable),
            )
        if rule.requires_protein and _count(pool, slot, PROTEIN_GROUP, usable) == 0:
            add(slot, FoodCategory.PROTEIN, _MIN_SLOT_OPTIONS)
            if _count(pool, slot, PROTEIN_GROUP, usable) == 0:
                add(slot, FoodCategory.DAIRY, _MIN_SLOT_OPTIONS)

    return allowed + extra if extra else allowed


# Cuántos alimentos de cada categoría se le enseñan al modelo. El catálogo
# entero (~170) cabe bien en Gemini; se recorta igual para no saturar el
# esquema Literal y dejar margen al crítico/recetas. Preferencias liked van
# primero (ver shortlist_for_llm).
LLM_SHORTLIST: dict[FoodCategory, int] = {
    FoodCategory.PROTEIN: 18,
    FoodCategory.CARB: 18,
    FoodCategory.FAT: 8,
    FoodCategory.FRUIT: 10,
    FoodCategory.DAIRY: 8,
    FoodCategory.VEGETABLE: 8,
    FoodCategory.OTHER: 4,
}


def shortlist_for_llm(allowed: list[FoodItem], *, liked: set[UUID] | None = None) -> list[FoodItem]:
    """Un catálogo corto y equilibrado para enseñarle al modelo.

    El modelo no necesita ver todo lo que existe: necesita suficiente variedad
    en cada rol para armar siete días distintos. Se prioriza lo que la persona
    marcó y luego se completa por orden alfabético, que es estable — el mismo
    perfil recibe el mismo catálogo y la caché por `input_hash` sigue sirviendo.

    Los alimentos libres entran siempre: no gastan casi tokens y sin ellos se
    pierden la ensalada y el café.
    """
    liked = liked or set()
    by_category: dict[FoodCategory, list[FoodItem]] = {}
    free: list[FoodItem] = []
    for food in sorted(allowed, key=lambda f: (f.id not in liked, f.name_es)):
        if food.is_free:
            free.append(food)
            continue
        by_category.setdefault(food.category, []).append(food)

    picked: list[FoodItem] = []
    for category, limit in LLM_SHORTLIST.items():
        picked.extend(by_category.get(category, [])[:limit])
    picked.extend(free)
    # Si el recorte dejara un rol sin nada, es mejor el pool entero que un menú
    # que no cuadra: el coste de tokens no vale un fallo de generación.
    return picked or allowed
