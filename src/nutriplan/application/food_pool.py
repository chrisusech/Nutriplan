"""El conjunto de alimentos con el que se le puede armar el menú a alguien.

Vivía copiado en cuatro sitios (generar, editar y dos pantallas del generador),
lo que hizo que "sorpréndeme" funcionara en uno y fallara en los otros tres.
"""

from uuid import UUID

import structlog

from nutriplan.domain.food_filter import allowed_foods
from nutriplan.domain.generation_rules import SLOT_STRUCTURE
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

# Solo carbo: es el macro que más se queda corto cuando el liked es arepa/pan.
# Proteína/lácteo suelen alcanzar con lo marcado; forzar whey/parmesano ensucia.
_COVER_CATEGORIES = (FoodCategory.CARB,)

# Dos opciones, no una, donde el carbohidrato es obligatorio (desayuno y almuerzo).
# Con una sola, el motor no tiene alternativa y sale el mismo carbo los siete días:
# le pasó a un cliente de 2.900 kcal cuyo almuerzo pedía 128 g de carbo y solo el
# arroz llegaba —la papa se topea en 100 g y el plátano en 87—, así que o repetía
# arroz o servía platos que la validación tumbaba.
_MIN_CARB_OPTIONS = 2

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
    return allowed_foods(liked, client.restrictions, banned)


def _minimo(slot: MealSlot, category: FoodCategory) -> int:
    """Cuántas opciones de esta categoría necesita el slot para armar la semana.

    Donde el carbohidrato es obligatorio hacen falta dos: con una sola, siete
    días solo pueden salir con el mismo carbo repetido.
    """
    if category is FoodCategory.CARB and SLOT_STRUCTURE[slot].requires_carb:
        return _MIN_CARB_OPTIONS
    return 1


def supplement_pool_for_targets(
    allowed: list[FoodItem],
    universe: list[FoodItem],
    *,
    daily: MacroTargets,
    config: NutritionConfig,
    disliked: set[str] | None = None,
    banned: set[UUID] | None = None,
) -> list[FoodItem]:
    """Si el pool liked no da para el slot, añade del universo lo mínimo.

    Caso típico: desayuno pide 128 g de carbo y solo hay arepa/pan (tope ~64 g).
    Sin avena u otro carbo en gramos el solver nunca cuadra. Preferencias siguen
    mandando; esto solo evita un menú imposible — o repetido hasta el aburrimiento,
    que es lo que sale cuando el slot tiene una única opción viable.
    """
    disliked = disliked or set()
    banned = banned or set()
    usable = usable_in_slot(daily, config)
    have = {f.id for f in allowed}
    extra: list[FoodItem] = []

    for slot in config.meal_distribution:
        for category in _COVER_CATEGORIES:
            tienen = sum(
                1
                for f in allowed
                if f.category is category and slot in f.meal_slots and usable(f, slot)
            )
            faltan = _minimo(slot, category) - tienen
            if faltan <= 0:
                continue
            candidates = sorted(
                (
                    f
                    for f in universe
                    if f.id not in have
                    and f.id not in banned
                    and f.category is category
                    and slot in f.meal_slots
                    and not matches_dislike(f, disliked)
                    and usable(f, slot)
                    and not (
                        category is FoodCategory.CARB
                        and any(s in f.name_es.lower() for s in _CARB_SUPPLEMENT_SKIP)
                    )
                ),
                key=lambda f: (
                    # Preferir carbos que se pesan (avena, arroz) sobre unidades.
                    0 if f.unit_granularity is UnitGranularity.GRAMS else 1,
                    -getattr(
                        f,
                        "carb_100g" if category is FoodCategory.CARB else "protein_100g",
                    ),
                    f.name_es,
                ),
            )
            for pick in candidates[:faltan]:
                extra.append(pick)
                have.add(pick.id)
                logger.info(
                    "pool_supplemented",
                    slot=slot.value,
                    category=category.value,
                    food=pick.name_es,
                )

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
