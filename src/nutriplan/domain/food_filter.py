"""Filtro determinista de restricciones (sección 10.4)."""

from uuid import UUID

from nutriplan.domain.models import FoodItem

# Vocabulario de tags de la base de alimentos.
KNOWN_TAGS = {
    "mariscos",
    "pescado",
    "gluten",
    "lacteo",
    "cerdo",
    "res",
    "huevo",
    "frutos_secos",
    "soya",
    "batido",
    "vegano",
    "condimento",
}

# Restricciones canónicas (como llegan del intake confirmado) → tags prohibidos.
RESTRICTION_TAG_MAP: dict[str, set[str]] = {
    "no_seafood": {"mariscos"},
    "no_fish": {"pescado", "mariscos"},
    "no_gluten": {"gluten"},
    "no_dairy": {"lacteo"},
    "no_lactose": {"lacteo"},
    "no_pork": {"cerdo"},
    "no_beef": {"res"},
    "no_eggs": {"huevo"},
    "no_nuts": {"frutos_secos"},
    "no_soy": {"soya"},
    "no_shake": {"batido"},
}


def forbidden_tags(restrictions: list[str]) -> tuple[set[str], list[str]]:
    """Resuelve restricciones a tags prohibidos.

    Acepta restricciones canónicas (RESTRICTION_TAG_MAP) o tags directos.
    Las no reconocidas se devuelven aparte para revisión humana — nunca se
    adivina qué prohíben.
    """
    tags: set[str] = set()
    unrecognized: list[str] = []
    for restriction in restrictions:
        key = restriction.strip().lower().replace(" ", "_")
        if key in RESTRICTION_TAG_MAP:
            tags |= RESTRICTION_TAG_MAP[key]
        elif key in KNOWN_TAGS:
            tags.add(key)
        else:
            unrecognized.append(restriction)
    return tags, unrecognized


def allowed_foods(
    liked: list[FoodItem],
    restrictions: list[str],
    banned_ids: set[UUID] | None = None,
) -> list[FoodItem]:
    """Conjunto permitido: preferencias ∩ no-restringidos ∩ no-baneados."""
    tags, unrecognized = forbidden_tags(restrictions)
    if unrecognized:
        raise ValueError(f"Restricciones no reconocidas: {unrecognized}")
    banned = banned_ids or set()
    return [food for food in liked if not (set(food.tags) & tags) and food.id not in banned]
