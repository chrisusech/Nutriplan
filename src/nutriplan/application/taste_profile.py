"""Interpretar lo que la persona escribió y convertirlo en gusto persistente.

El bucle que hace que el plan mejore: la semana se cierra con comentarios, la IA
los traduce a alimentos concretos del catálogo (nunca a nombres libres: el enum
del esquema no la deja), y esas señales entran en la generación de la siguiente.

La IA aquí no decide nada del plan — solo lee castellano y señala alimentos.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, create_model

from nutriplan.application.prompts import load_prompt
from nutriplan.domain.critique import food_aliases
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import Client, FoodItem
from nutriplan.domain.taste import (
    MAX_ITEMS_IN_PROMPT,
    RatedDish,
    TasteProfile,
    build_taste_profile,
)
from nutriplan.ports.llm_client import LLMClient

logger = structlog.get_logger(__name__)

TASTE_PROMPT_VERSION = 1
MAX_ADJUSTMENTS = 4


class TasteSignalStore(Protocol):
    async def upsert(
        self,
        *,
        client_id: UUID,
        week_start: date,
        avoid_food_ids: list[UUID],
        prefer_food_ids: list[UUID],
        adjustments: list[str],
        model: str | None = None,
        source: str = "ai",
    ) -> None: ...


class AccumulatedSignals(Protocol):
    avoid_food_ids: list[UUID]
    prefer_food_ids: list[UUID]
    adjustments: list[str]


class RatingHistory(Protocol):
    async def rated_dishes(self, *, limit: int = 200) -> list[RatedDish]: ...
    async def comments_for_plan(self, plan_cycle_id: UUID) -> list[tuple[str, int, str]]: ...


class TasteSignalReader(Protocol):
    async def accumulated_for(self, client_id: UUID, *, weeks: int = 8) -> AccumulatedSignals: ...


def build_taste_schema(allowed: list[FoodItem]) -> type[BaseModel]:
    """Esquema con el enum de alias: la IA no puede nombrar lo que no existe."""
    aliases = tuple(food_aliases(allowed))
    alias_literal = Literal[aliases]  # type: ignore[valid-type]
    return create_model(
        "TasteSignalsStrict",
        __config__=ConfigDict(extra="forbid"),
        avoid=(list[alias_literal], Field(default_factory=list, max_length=8)),
        prefer=(list[alias_literal], Field(default_factory=list, max_length=8)),
        adjustments=(
            list[str],
            Field(default_factory=list, max_length=MAX_ADJUSTMENTS),
        ),
    )


def build_taste_prompt(
    *,
    weekly_comment: str | None,
    dish_comments: list[tuple[str, int, str]],
    allowed: list[FoodItem],
) -> str:
    aliases = food_aliases(allowed)
    id_by_food = {v: k for k, v in aliases.items()}
    parts = ["Lo que escribió la persona sobre su semana:"]
    parts.append((weekly_comment or "").strip() or "(no escribió nada)")
    if dish_comments:
        parts += ["", "Comentarios por plato:"]
        parts += [
            f"- {name} (nota {rating}/5): {comment}" for name, rating, comment in dish_comments[:20]
        ]
    parts += ["", "CATÁLOGO PERMITIDO (usa exclusivamente estos alias):"]
    parts += [
        f"- {id_by_food[f.id]}: {f.name_es}"
        for f in sorted(allowed, key=lambda f: f.name_es)
        if f.id in id_by_food
    ]
    return "\n".join(parts)


async def extract_taste_signals(
    *,
    client: Client,
    plan_cycle_id: UUID | None,
    week_start: date,
    weekly_comment: str | None,
    allowed: list[FoodItem],
    ratings: RatingHistory,
    store: TasteSignalStore,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
) -> bool:
    """Traduce los comentarios de la semana a señales. Soft-fail a propósito.

    Que la IA no esté disponible no puede impedir cerrar la semana: se pierde el
    matiz de la prosa, pero las notas numéricas siguen contando.
    """
    if llm is None or not model or not allowed:
        return False
    dish_comments = await ratings.comments_for_plan(plan_cycle_id) if plan_cycle_id else []
    if not (weekly_comment or "").strip() and not dish_comments:
        return False

    aliases = food_aliases(allowed)
    try:
        system = load_prompt(prompts_dir, "taste_signals", TASTE_PROMPT_VERSION).text
        text = build_taste_prompt(
            weekly_comment=weekly_comment,
            dish_comments=dish_comments,
            allowed=allowed,
        )
        raw = await llm.extract(
            system=system, text=text, schema=build_taste_schema(allowed), model=model
        )
    except LLMError as exc:
        logger.warning("taste_signals_skipped", error=str(exc))
        return False

    data = raw.model_dump()
    await store.upsert(
        client_id=client.id,
        week_start=week_start,
        avoid_food_ids=[aliases[a] for a in data.get("avoid", []) if a in aliases],
        prefer_food_ids=[aliases[a] for a in data.get("prefer", []) if a in aliases],
        adjustments=[str(a).strip()[:80] for a in data.get("adjustments", []) if str(a).strip()][
            :MAX_ADJUSTMENTS
        ],
        model=model,
    )
    logger.info(
        "taste_signals_stored",
        client_id=str(client.id),
        avoid=len(data.get("avoid", [])),
        prefer=len(data.get("prefer", [])),
    )
    return True


async def taste_profile_for(
    *,
    client: Client,
    ratings: RatingHistory,
    signals: TasteSignalReader,
) -> TasteProfile:
    """Todo lo que sabemos de su gusto: las notas más lo leído en sus comentarios."""
    accumulated = await signals.accumulated_for(client.id)
    return build_taste_profile(
        await ratings.rated_dishes(),
        avoid_food_ids=accumulated.avoid_food_ids[:MAX_ITEMS_IN_PROMPT],
        prefer_food_ids=accumulated.prefer_food_ids[:MAX_ITEMS_IN_PROMPT],
        adjustments=accumulated.adjustments,
    )
