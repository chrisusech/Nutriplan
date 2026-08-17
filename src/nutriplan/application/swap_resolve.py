"""Frase coloquial → alimentos del catálogo → el motor porciona.

La IA nombra alias del enum; el código completa proteína/carbo del slot y
calcula gramos. Si no hay modelo (o 429), el mapeo de palabras hace lo mismo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from nutriplan.application.swap_meal import complete_dish, swap_slot
from nutriplan.application.swap_pool import pool_for_note
from nutriplan.application.swap_wish import interpret_swap_wish
from nutriplan.domain.errors import GenerationError, ValidationError
from nutriplan.domain.meal_template import Dish
from nutriplan.domain.models import DayPlan, FoodItem, MacroTargets, MealEntry, MealSlot
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.swap_note import (
    SwapIntent,
    dish_avoids_needles,
    dish_from_foods,
    foods_from_note,
    parse_swap_note,
    without_excluded,
)
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.llm_client import LLMClient

NO_SWAP = "No pudimos cambiar el plato con esa petición. Pruebe con otras palabras."
NO_RANK = "No hay otro plato que encaje en esa comida."


class SlotRanker(Protocol):
    def rank_slot(
        self,
        slot: MealSlot,
        *,
        exclude_keys: frozenset[str] = frozenset(),
        today_ids: frozenset[str] = frozenset(),
        note: str = "",
    ) -> list[Dish]: ...


@dataclass(frozen=True)
class SwapResult:
    day: DayPlan
    pick: Dish
    extra_foods: tuple[FoodItem, ...]


async def resolve_swap(
    *,
    day: DayPlan,
    slot: MealSlot,
    meal: MealEntry,
    wish: str,
    foods: list[FoodItem],
    daily: MacroTargets,
    config: NutritionConfig,
    engine: SlotRanker,
    food_repo: FoodRepository,
    restrictions: list[str],
    banned: set[UUID] | None = None,
    llm: LLMClient | None = None,
    prompts_dir: Path | None = None,
    model: str = "",
) -> SwapResult:
    """Pool → IA → mapeo → completar slot → solver, con reintento."""
    note = wish.strip()
    extra: list[FoodItem] = []
    pool = foods
    if note:
        pool = await pool_for_note(
            note=note,
            base=foods,
            food_repo=food_repo,
            restrictions=restrictions,
            banned=banned,
        )
        extra = pool[len(foods) :]
    foods_by_id = {food.id: food for food in pool}
    intent = parse_swap_note(note, pool) if note else None
    today_ids = frozenset(
        str(item.food_id)
        for other in day.meals
        if other.slot is not slot
        for item in other.items
        if item.food_id
    )
    exclude = frozenset(filter(None, [meal.dish_key]))
    for pick in _unique(
        await _candidates(
            slot=slot,
            meal=meal,
            note=note,
            pool=pool,
            foods_by_id=foods_by_id,
            intent=intent,
            engine=engine,
            exclude=exclude,
            today_ids=today_ids,
            llm=llm,
            prompts_dir=prompts_dir,
            model=model,
        )
    ):
        try:
            new_day = swap_slot(
                day,
                slot=slot,
                pick=pick,
                foods_by_id=foods_by_id,
                daily=daily,
                config=config,
            )
        except GenerationError:
            continue
        return SwapResult(day=new_day, pick=pick, extra_foods=tuple(extra))
    raise ValidationError(NO_SWAP if note else NO_RANK)


async def _candidates(
    *,
    slot: MealSlot,
    meal: MealEntry,
    note: str,
    pool: list[FoodItem],
    foods_by_id: dict[UUID, FoodItem],
    intent: SwapIntent | None,
    engine: SlotRanker,
    exclude: frozenset[str],
    today_ids: frozenset[str],
    llm: LLMClient | None,
    prompts_dir: Path | None,
    model: str,
) -> list[Dish]:
    out: list[Dish] = []
    keep_same = False
    rank_note = note
    if intent is not None:
        keep_same = intent.keep_same
        rank_note = "" if keep_same else intent.include_note
        if keep_same:
            kept = _keep_same(slot, meal, foods_by_id, intent, pool)
            if kept is not None:
                out.append(kept)
    if note and not keep_same:
        mapped_foods = _mapped(note, pool, intent)
        mapped = complete_dish(slot, mapped_foods, pool, note=note)
        if mapped is not None:
            out.append(mapped)
        # La IA solo si el código no reconoció alimentos. No esperamos un 429
        # para armar un plato que el catálogo ya sabía nombrar.
        if not mapped_foods and llm is not None and prompts_dir is not None:
            ai = await interpret_swap_wish(
                note=note,
                slot=slot,
                allowed=pool,
                llm=llm,
                prompts_dir=prompts_dir,
                model=model,
            )
            filled = (
                complete_dish(
                    slot,
                    list(ai.foods),
                    pool,
                    note=note,
                    name=ai.name,
                    free_salad=ai.free_salad,
                )
                if ai is not None
                else None
            )
            if filled is not None:
                out.append(filled)
        return out
    ranked = engine.rank_slot(slot, exclude_keys=exclude, today_ids=today_ids, note=rank_note)
    if intent is not None and intent.exclude:
        ranked = [dish for dish in ranked if dish_avoids_needles(dish, intent.exclude)]
    out.extend(ranked)
    return out


def _keep_same(
    slot: MealSlot,
    meal: MealEntry,
    foods_by_id: dict[UUID, FoodItem],
    intent: SwapIntent,
    pool: list[FoodItem],
) -> Dish | None:
    current = [
        foods_by_id[item.food_id]
        for item in meal.items
        if item.food_id and item.food_id in foods_by_id
    ]
    kept = without_excluded(current, intent.exclude)
    if intent.include_note.strip():
        extra = _mapped(intent.include_note, pool, intent)
        seen = {food.id for food in kept}
        kept.extend(food for food in extra if food.id not in seen)
    if not kept or {food.id for food in kept} == {food.id for food in current}:
        return None
    return dish_from_foods(slot, kept)


def _mapped(note: str, pool: list[FoodItem], intent: SwapIntent | None) -> list[FoodItem]:
    found = foods_from_note(note, pool)
    return found if intent is None else without_excluded(found, intent.exclude)


def _unique(dishes: Sequence[Dish]) -> list[Dish]:
    seen: set[tuple[UUID, ...]] = set()
    out: list[Dish] = []
    for dish in dishes:
        if dish.food_ids in seen:
            continue
        seen.add(dish.food_ids)
        out.append(dish)
    return out
