"""Cuando el catálogo no tiene el plato, la IA interpreta la nota.

El código sigue dueño de los números: el modelo solo nombra alimentos del
enum. Si no hay proveedor o el schema falla, se vuelve None y el borde
se lo dice a la persona.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from uuid import UUID

import structlog
from pydantic import BaseModel, ConfigDict, Field, create_model

from nutriplan.application.prompts import load_prompt
from nutriplan.domain.critique import food_aliases
from nutriplan.domain.errors import LLMError
from nutriplan.domain.meal_template import Dish
from nutriplan.domain.models import FoodItem, MealSlot
from nutriplan.domain.swap_note import dish_from_foods
from nutriplan.ports.llm_client import LLMClient

logger = structlog.get_logger(__name__)

SWAP_WISH_VERSION = 1


def _schema(allowed: list[FoodItem]) -> type[BaseModel]:
    aliases = tuple(food_aliases(allowed))
    alias_lit = Literal[aliases]  # type: ignore[valid-type]
    return create_model(
        "SwapWishStrict",
        __config__=ConfigDict(extra="forbid"),
        food_ids=(list[alias_lit], Field(min_length=1, max_length=4)),
        dish_name=(str, Field(max_length=80)),
        free_salad=(bool, False),
    )


def _prompt(note: str, slot: MealSlot, allowed: list[FoodItem]) -> str:
    aliases = food_aliases(allowed)
    by_id = {fid: alias for alias, fid in aliases.items()}
    lines = [
        f"Comida: {slot.value}",
        f"Pedido: {note.strip()[:400]}",
        "",
        "CATÁLOGO (usa solo estos alias):",
    ]
    lines += [
        f"- {by_id[food.id]}: {food.name_es}"
        for food in sorted(allowed, key=lambda f: f.name_es)
        if food.id in by_id
    ]
    return "\n".join(lines)


async def interpret_swap_wish(
    *,
    note: str,
    slot: MealSlot,
    allowed: list[FoodItem],
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
) -> Dish | None:
    """Traduce la nota a un plato. None si no hay IA o no se pudo leer."""
    usable = [f for f in allowed if slot in f.meal_slots] or allowed
    if llm is None or not model or not usable or not note.strip():
        return None
    try:
        system = load_prompt(prompts_dir, "swap_wish", SWAP_WISH_VERSION).text
        raw = await llm.extract(
            system=system,
            text=_prompt(note, slot, usable),
            schema=_schema(usable),
            model=model,
        )
    except (LLMError, ValueError) as exc:
        logger.warning("swap_wish_skipped", error=str(exc))
        return None
    aliases = food_aliases(usable)
    ids: list[UUID] = []
    for alias in raw.food_ids:  # type: ignore[attr-defined]
        fid = aliases.get(str(alias))
        if fid is not None and fid not in ids:
            ids.append(fid)
    by_id = {f.id: f for f in usable}
    foods = [by_id[i] for i in ids if i in by_id]
    dish = dish_from_foods(slot, foods, name=str(raw.dish_name))  # type: ignore[attr-defined]
    if dish is None:
        return None
    if raw.free_salad:  # type: ignore[attr-defined]
        return Dish(
            template_id=dish.template_id,
            name=dish.name,
            slot=dish.slot,
            foods=dish.foods,
            free_salad=True,
        )
    return dish
