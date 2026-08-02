"""Crear el perfil nutricional de una cuenta.

Sustituye al intake de Word y al importador del formulario: la persona se
describe a sí misma dentro de la app. Lo que antes tenía que adivinar un LLM
leyendo un documento ahora llega como datos.
"""

from uuid import UUID, uuid4

import structlog

from nutriplan.domain.errors import ValidationError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    MealSlot,
    Sex,
)
from nutriplan.ports.repository import ClientRepository

logger = structlog.get_logger(__name__)

# Topes de las entradas libres. Van al prompt del selector y del crítico, así que
# sin límite serían superficie de inyección y de coste.
MAX_FREE_TEXT = 2000
MAX_DISLIKES = 30
MAX_TAG_LEN = 60

CONTEXT_TAGS = ("cocina", "come_afuera", "entrena_noche", "come_rapido")


def _clean_tags(raw: list[str], allowed: tuple[str, ...] | None = None) -> list[str]:
    seen: list[str] = []
    for tag in raw:
        tag = tag.strip().lower()[:MAX_TAG_LEN]
        if not tag or tag in seen:
            continue
        if allowed is not None and tag not in allowed:
            continue
        seen.append(tag)
    return seen[:MAX_DISLIKES]


async def create_profile(
    *,
    account_id: UUID,
    tenant_id: UUID,
    name: str,
    sex: Sex,
    age_years: int,
    height_cm: float,
    weight_kg: float,
    goal: Goal,
    activity_level: ActivityLevel,
    meal_slots: list[MealSlot],
    client_repo: ClientRepository,
    city: str | None = None,
    country: str | None = None,
    liked_food_ids: list[UUID] | None = None,
    restrictions: list[str] | None = None,
    dislikes: list[str] | None = None,
    context_tags: list[str] | None = None,
    eating_pattern_raw: str | None = None,
    free_meal_day: int | None = None,
    free_meal_slot: MealSlot | None = None,
) -> Client:
    """El perfil con el que se genera el menú. Uno por cuenta.

    `liked_food_ids` vacío es una respuesta válida y esperada: significa
    "sorpréndeme", y deja que el catálogo entero (menos restricciones y
    dislikes) esté disponible. Obligar a marcar alimentos era justo lo que
    agobiaba en la versión anterior.
    """
    if await client_repo.get_by_user(account_id) is not None:
        raise ValidationError("Esta cuenta ya tiene un perfil")

    client = Client(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=account_id,
        name=name.strip(),
        sex=sex,
        age_years=age_years,
        height_cm=height_cm,
        weight_kg=weight_kg,
        goal=goal,
        activity_level=activity_level,
        city=(city or "").strip()[:120] or None,
        country=(country or "").strip().upper()[:2] or None,
        liked_food_ids=liked_food_ids or [],
        restrictions=_clean_tags(restrictions or []),
        dislikes=_clean_tags(dislikes or []),
        context_tags=_clean_tags(context_tags or [], allowed=CONTEXT_TAGS),
        eating_pattern_raw=(eating_pattern_raw or "").strip()[:MAX_FREE_TEXT] or None,
        meal_slots=meal_slots,
        free_meal_day=free_meal_day,
        free_meal_slot=free_meal_slot,
    )
    await client_repo.add(client)
    logger.info(
        "profile_created",
        tenant_id=str(tenant_id),
        meals=len(client.meal_slots),
        has_habits=client.eating_pattern_raw is not None,
        picked_foods=len(client.liked_food_ids),
    )
    return client
