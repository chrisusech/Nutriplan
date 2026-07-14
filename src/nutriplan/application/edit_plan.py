"""Edición quirúrgica del plan (Fase 6): un slot/día sin regenerar la semana."""

from datetime import UTC, datetime
from uuid import UUID

from nutriplan.domain import meal_affinity
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.food_filter import allowed_foods
from nutriplan.domain.generation_rules import SLOT_STRUCTURE
from nutriplan.domain.macro_split import daily_minus_free_meal, free_meal_slot_of
from nutriplan.domain.models import (
    Client,
    DayPlan,
    FoodItem,
    MealEntry,
    MealFoodPortion,
    MealItem,
    MealSlot,
    NutritionTargets,
    PlanCycle,
    PlanPhase,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import (
    SolvedMeal,
    macros_of,
    rebalance_day_after_edit,
    solve_day_portions,
)
from nutriplan.domain.validation import day_totals
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.repository import ClientRepository, PlanRepository

_SLOT_ORDER = list(MealSlot)


def _day_of(cycle: PlanCycle, phase: PlanPhase, day_index: int) -> DayPlan:
    for day in cycle.days:
        if day.phase is phase and day.day_index == day_index:
            return day
    raise GenerationError(f"Día {day_index} de fase {phase.value} no existe en el plan")


def _locked_keys(
    day: DayPlan, edited_slot: MealSlot | None = None
) -> frozenset[tuple[MealSlot, str]]:
    keys: set[tuple[MealSlot, str]] = set()
    for meal in day.meals:
        for item in meal.items:
            if item.is_locked and item.food_id and item.grams and item.grams > 0:
                keys.add((meal.slot, str(item.food_id)))
        if edited_slot is not None and meal.slot is edited_slot:
            for item in meal.items:
                if item.food_id and item.grams and item.grams > 0:
                    keys.add((meal.slot, str(item.food_id)))
    return frozenset(keys)


def _meals_input(
    day: DayPlan, foods: dict[UUID, FoodItem]
) -> list[tuple[MealSlot, list[FoodItem]]]:
    out: list[tuple[MealSlot, list[FoodItem]]] = []
    for meal in sorted(day.meals, key=lambda m: _SLOT_ORDER.index(m.slot)):
        slot_foods = [
            foods[item.food_id]
            for item in meal.items
            if item.food_id and not item.is_free
        ]
        if slot_foods:
            out.append((meal.slot, slot_foods))
    return out


def _grams_map(day: DayPlan) -> dict[tuple[MealSlot, str], float]:
    grams: dict[tuple[MealSlot, str], float] = {}
    for meal in day.meals:
        for item in meal.items:
            if item.food_id and item.grams and item.grams > 0 and not item.is_free:
                grams[(meal.slot, str(item.food_id))] = item.grams
    return grams


def _needs_full_solve(day: DayPlan) -> bool:
    return any(
        item.food_id and not item.is_free and (item.grams is None or item.grams <= 0)
        for meal in day.meals
        for item in meal.items
    )


def _solved_to_day(
    day_index: int,
    phase: PlanPhase,
    solved: list[SolvedMeal],
    *,
    free_flags: dict[MealSlot, bool],
    preserve: DayPlan | None = None,
    lock_edited_slot: MealSlot | None = None,
) -> DayPlan:
    preserve_meals = {m.slot: m for m in preserve.meals} if preserve else {}
    preserve_items: dict[MealSlot, dict[UUID, MealItem]] = {}
    for slot, meal in preserve_meals.items():
        preserve_items[slot] = {item.food_id: item for item in meal.items if item.food_id}

    meals: list[MealEntry] = []
    for m in sorted(solved, key=lambda x: _SLOT_ORDER.index(x.slot)):
        old_meal = preserve_meals.get(m.slot)
        old_items = preserve_items.get(m.slot, {})
        items: list[MealItem] = []
        for i, p in enumerate(m.portions):
            prev = old_items.get(p.food_id)
            locked = prev.is_locked if prev else False
            if lock_edited_slot is not None and m.slot is lock_edited_slot:
                locked = True
            items.append(
                MealItem(
                    id=prev.id if prev else None,
                    food_id=p.food_id,
                    grams=p.grams,
                    position=i,
                    is_locked=locked,
                )
            )
        meals.append(
            MealEntry(
                id=old_meal.id if old_meal else None,
                slot=m.slot,
                items=items,
                computed=m.computed,
                free_salad=free_flags.get(m.slot, False)
                or SLOT_STRUCTURE[m.slot].free_salad_default,
            )
        )

    # La comida libre no está en `solved` —no tiene alimentos que porcionar— así que
    # reconstruir el día desde el solver la BORRARÍA: cualquier ajuste de gramos en
    # otra comida se la llevaría por delante, en silencio. Se reinserta en su sitio.
    free_meal = next((m for m in preserve_meals.values() if m.is_free_meal), None)
    if free_meal is not None:
        meals.append(free_meal)
        meals.sort(key=lambda m: _SLOT_ORDER.index(m.slot))

    return DayPlan(day_index=day_index, phase=phase, meals=meals, totals=day_totals(solved))


async def resolve_day(
    day: DayPlan,
    foods: dict[UUID, FoodItem],
    targets: NutritionTargets,
    config: NutritionConfig,
    *,
    edited_slot: MealSlot | None = None,
) -> DayPlan:
    """Re-solve un día respetando ítems bloqueados."""
    meals_input = _meals_input(day, foods)
    if not meals_input:
        raise GenerationError("El día no tiene alimentos porcionables")

    free_flags = {m.slot: m.free_salad for m in day.meals}

    # Si el día tiene comida libre, el objetivo contra el que se re-porciona es el
    # reducido — el mismo con el que se generó. Con el objetivo completo, las cuatro
    # comidas restantes tendrían que cargar con el día entero y editar un gramo
    # engordaría el resto del día.
    day_daily = daily_minus_free_meal(targets.daily, config, free_meal_slot_of(day))

    # Un slot sin fuente de proteína (un snack de solo fruta) ya no debe proteína:
    # `macro_split.macro_shares` le da cuota 0 y la reparte entre las comidas que
    # sí la llevan. Antes había que decirle al reparador que la relajara a mano.
    if _needs_full_solve(day):
        solved = solve_day_portions(meals_input, day_daily, config)
    else:
        locked = _locked_keys(day, edited_slot)
        grams = rebalance_day_after_edit(
            meals_input,
            _grams_map(day),
            day_daily,
            config,
            locked=locked,
        )
        solved = []
        for slot, slot_foods in meals_input:
            pairs: list[tuple[FoodItem, float]] = []
            portions: list[MealFoodPortion] = []
            for f in slot_foods:
                g = grams.get((slot, str(f.id)), 0.0)
                if g > 0:
                    portions.append(MealFoodPortion(food_id=f.id, grams=g))
                    pairs.append((f, g))
            if portions:
                solved.append(
                    SolvedMeal(slot=slot, portions=portions, computed=macros_of(pairs))
                )

    return _solved_to_day(
        day.day_index,
        day.phase,
        solved,
        free_flags=free_flags,
        preserve=day,
        lock_edited_slot=edited_slot,
    )


async def swap_food_in_slot(
    *,
    cycle: PlanCycle,
    phase: PlanPhase,
    day_index: int,
    slot: MealSlot,
    old_food_id: UUID,
    new_food: FoodItem,
    foods: dict[UUID, FoodItem],
    targets: NutritionTargets,
    config: NutritionConfig,
) -> DayPlan:
    day = _day_of(cycle, phase, day_index)
    meal = next((m for m in day.meals if m.slot is slot), None)
    if meal is None:
        raise GenerationError(f"Slot {slot.value} no existe en el día")
    if meal.is_free_meal:
        # En la pantalla esa celda no ofrece controles, pero el endpoint es público.
        raise GenerationError("La comida libre no tiene alimentos que cambiar")

    replaced = False
    new_items: list[MealItem] = []
    for item in meal.items:
        if item.food_id == old_food_id:
            new_items.append(
                MealItem(
                    id=item.id,
                    food_id=new_food.id,
                    grams=item.grams,
                    position=item.position,
                    is_locked=False,
                )
            )
            replaced = True
        else:
            new_items.append(item)
    if not replaced:
        raise GenerationError("El alimento a reemplazar no está en esa comida")

    meal.items = new_items
    foods[new_food.id] = new_food
    return await resolve_day(day, foods, targets, config, edited_slot=slot)


async def remove_food_from_slot(
    *,
    cycle: PlanCycle,
    phase: PlanPhase,
    day_index: int,
    slot: MealSlot,
    food_id: UUID,
    foods: dict[UUID, FoodItem],
    targets: NutritionTargets,
    config: NutritionConfig,
) -> DayPlan:
    day = _day_of(cycle, phase, day_index)
    meal = next((m for m in day.meals if m.slot is slot), None)
    if meal is None:
        raise GenerationError(f"Slot {slot.value} no existe en el día")
    if meal.is_free_meal:
        raise GenerationError("La comida libre no tiene alimentos que quitar")

    remaining = [item for item in meal.items if item.food_id != food_id]
    if len(remaining) == len(meal.items):
        raise GenerationError("El alimento no está en esa comida")
    if not remaining:
        raise GenerationError("La comida no puede quedar vacía")

    meal.items = remaining
    return await resolve_day(day, foods, targets, config, edited_slot=slot)


async def ban_for_client(
    *,
    client_repo: ClientRepository,
    client_id: UUID,
    food_id: UUID,
) -> None:
    await client_repo.ban_food(client_id, food_id)


async def validate_swap_food(
    *,
    client: Client,
    food_repo: FoodRepository,
    client_repo: ClientRepository,
    new_food: FoodItem,
    slot: MealSlot,
) -> None:
    """El reemplazo debe estar permitido y encajar en la comida."""
    liked = await food_repo.get_by_ids(client.liked_food_ids)
    banned = set(await client_repo.list_banned_food_ids(client.id))
    pool = allowed_foods(liked, client.restrictions, banned)
    if not any(f.id == new_food.id for f in pool):
        raise GenerationError("Ese alimento no está permitido para este cliente")
    if not meal_affinity.allows(new_food, slot):
        raise GenerationError("Ese alimento no encaja en esta comida")


async def persist_day_edit(
    *,
    plan_repo: PlanRepository,
    cycle: PlanCycle,
    phase: PlanPhase,
    day_index: int,
    day: DayPlan,
    edited_by: UUID | None = None,
) -> None:
    await plan_repo.update_day(
        cycle.id, phase, day_index, day, mark_edited=True, edited_by=edited_by
    )
    cycle.edited_at = datetime.now(UTC)
    if edited_by:
        cycle.edited_by = edited_by
