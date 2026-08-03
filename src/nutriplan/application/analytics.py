"""Registrar qué pasa en la app, para poder mejorarla.

Dos reglas que no se negocian:

1. **Sin consentimiento no se registra nada.** La cuenta tiene que haber
   aceptado; si no, el evento se descarta en silencio.
2. **Sin PII.** Aquí van nombres de evento y propiedades agregables. Lo que
   identifica a alguien vive en sus tablas, no en el registro de uso — que es
   justo lo que permite anonimizar al borrar la cuenta en vez de perderlo todo.
"""

from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

import structlog

logger = structlog.get_logger(__name__)

# Propiedades que jamás deben acabar en un evento. La lista es corta a
# propósito: si hace falta alargarla, es que algo se está registrando mal.
FORBIDDEN_PROPS = frozenset({"email", "name", "nombre", "correo", "password", "token"})


class Event(StrEnum):
    """El embudo, paso a paso. Añadir uno aquí es declarar que se mide."""

    SIGNED_UP = "signed_up"
    CONSENT_GIVEN = "consent_given"
    ONBOARDING_DONE = "onboarding_done"
    MENU_GENERATED = "menu_generated"
    MENU_REFINED = "menu_refined"
    RECIPE_OPENED = "recipe_opened"
    DISH_RATED = "dish_rated"
    FEEDBACK_SENT = "feedback_sent"
    ACCOUNT_DELETED = "account_deleted"


class EventRepository(Protocol):
    async def record(
        self,
        *,
        name: str,
        props: dict[str, Any],
        user_id: UUID | None,
        tenant_id: UUID | None,
        platform: str | None,
    ) -> None: ...


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    """Quita lo que no debería estar. Mejor perder una propiedad que un dato
    personal en una tabla pensada para agregarse."""
    dirty = {k for k in props if k.lower() in FORBIDDEN_PROPS}
    if dirty:
        logger.warning("analytics_props_dropped", keys=sorted(dirty))
    return {k: v for k, v in props.items() if k.lower() not in FORBIDDEN_PROPS}


async def track(
    event: Event,
    *,
    repo: EventRepository,
    has_consent: bool,
    user_id: UUID | None = None,
    tenant_id: UUID | None = None,
    platform: str | None = None,
    **props: Any,
) -> None:
    """Registra un evento, si esa persona dejó."""
    if not has_consent:
        return
    await repo.record(
        name=event.value,
        props=_clean(props),
        user_id=user_id,
        tenant_id=tenant_id,
        platform=platform,
    )
