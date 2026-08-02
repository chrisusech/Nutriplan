"""Aplicar la crítica de la IA sin dejarle tocar un solo número.

La IA propone: cambiar este alimento por aquel, llamar a esto "Tostada de huevos
con aguacate". El código decide: recalcula los gramos con el mismo solver de
siempre y **descarta el cambio si el día se sale de tolerancia**.

Es una función pura a propósito. La llamada al LLM vive en el caso de uso; aquí
solo hay reglas, así que la garantía se puede probar sin un solo mock.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, create_model

from nutriplan.domain.macro_split import daily_minus_free_meal, free_meal_slot_of
from nutriplan.domain.models import (
    DayPlan,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import solve_day_portions
from nutriplan.domain.validation import day_totals, validate_day

# Un nombre de plato largo no cabe en una tarjeta de móvil.
MAX_DISH_NAME = 60


def food_aliases(allowed: Sequence[FoodItem]) -> dict[str, UUID]:
    """Alias cortos ("f7") para los alimentos, estables por orden de id.

    Un UUID cuesta ~20 tokens y aparece dos veces por alimento (en el enum del
    schema y en el prompt). Con un catálogo de 44 eso son ~1.800 tokens solo de
    identificadores, y el nivel gratis de Groq da 8.000 POR MINUTO. Con alias
    baja a menos de 200.
    """
    return {
        f"f{i}": food.id
        for i, food in enumerate(sorted(allowed, key=lambda f: str(f.id)))
    }


def build_critique_schema(allowed: Sequence[FoodItem]) -> type[BaseModel]:
    """El schema de la crítica, con los alias permitidos como enum.

    Mismo truco que `build_selection_schema`: la IA no puede *nombrar* un
    alimento prohibido, así que un swap fuera del catálogo no es algo que haya
    que detectar — es algo que no se puede expresar.
    """
    if not allowed:
        raise ValueError("El conjunto permitido está vacío")
    ids = tuple(food_aliases(allowed))
    food_id_literal = Literal[ids]  # type: ignore[valid-type]

    note_model = create_model(
        "MealCritique",
        __config__=ConfigDict(extra="forbid"),
        day_index=(int, Field(ge=0, le=6)),
        slot=(MealSlot, ...),
        dish_name=(str, Field(max_length=MAX_DISH_NAME)),
        swap_out_food_id=(food_id_literal | None, None),
        swap_in_food_id=(food_id_literal | None, None),
        reason=(str, Field(max_length=200)),
    )
    return create_model(
        "PlanCritique",
        __config__=ConfigDict(extra="forbid"),
        meals=(list[note_model], Field(max_length=64)),  # type: ignore[valid-type]
    )


@dataclass(frozen=True)
class RejectedSwap:
    """Un cambio que la IA pidió y el código no aceptó."""

    day_index: int
    slot: MealSlot
    reason: str


@dataclass(frozen=True)
class CritiqueOutcome:
    days: list[DayPlan]
    renamed: int
    applied: int
    rejected: list[RejectedSwap]


def _foods_of(meal: MealEntry, catalog: dict[UUID, FoodItem]) -> list[FoodItem]:
    return [catalog[i.food_id] for i in meal.items if i.food_id and i.food_id in catalog]


def _rebuild_day(
    day: DayPlan,
    foods_by_slot: dict[MealSlot, list[FoodItem]],
    daily: MacroTargets,
    config: NutritionConfig,
) -> DayPlan | None:
    """Re-resuelve el día completo. None si no cuadra dentro de tolerancia."""
    solvable = [(slot, foods) for slot, foods in foods_by_slot.items() if foods]
    try:
        solved = solve_day_portions(solvable, daily, config)
    except Exception:
        return None

    by_slot = {s.slot: s for s in solved}
    meals: list[MealEntry] = []
    for original in day.meals:
        if original.is_free_meal or original.slot not in by_slot:
            meals.append(original)
            continue
        s = by_slot[original.slot]
        meals.append(
            original.model_copy(
                update={
                    "items": [
                        MealItem(food_id=p.food_id, grams=p.grams, position=i)
                        for i, p in enumerate(s.portions)
                    ],
                    "computed": s.computed,
                }
            )
        )

    if validate_day(meals, daily, config):
        return None
    return day.model_copy(update={"meals": meals, "totals": day_totals(meals)})


def apply_critique(
    days: Sequence[DayPlan],
    critique: BaseModel,
    catalog: dict[UUID, FoodItem],
    config: NutritionConfig,
    daily: MacroTargets,
    aliases: dict[str, UUID] | None = None,
) -> CritiqueOutcome:
    """Aplica lo que la IA propuso, cambio a cambio, y rechaza lo que no cuadre.

    Los nombres de plato entran siempre: son lenguaje, y el lenguaje es lo que
    la IA sí decide. Los swaps entran de uno en uno y solo si el día sigue
    dentro de tolerancia después de re-resolver los gramos.
    """
    alias_to_id = aliases or food_aliases(list(catalog.values()))

    def resolve(alias: str | None) -> UUID | None:
        if alias is None:
            return None
        return alias_to_id.get(alias)

    notes = {(n.day_index, n.slot): n for n in critique.meals}  # type: ignore[attr-defined]
    result: list[DayPlan] = []
    renamed = applied = 0
    rejected: list[RejectedSwap] = []

    for day in sorted(days, key=lambda d: d.day_index):
        current = day
        day_daily = daily_minus_free_meal(daily, config, free_meal_slot_of(day))

        for meal in day.meals:
            note = notes.get((day.day_index, meal.slot))
            if note is None or meal.is_free_meal:
                continue

            out_id = resolve(note.swap_out_food_id)
            in_id = resolve(note.swap_in_food_id)
            if out_id and in_id:
                candidate = _with_swap(current, meal.slot, out_id, in_id, catalog)
                rebuilt = (
                    _rebuild_day(current, candidate, day_daily, config)
                    if candidate is not None
                    else None
                )
                if rebuilt is None:
                    rejected.append(
                        RejectedSwap(day.day_index, meal.slot, note.reason or "")
                    )
                else:
                    current = rebuilt
                    applied += 1

        # El nombre se pone al final, sobre el día que de verdad quedó, y solo
        # si sigue describiéndolo. Al re-resolver, el solver puede dejar fuera un
        # alimento: sin esta comprobación quedaba "durazno con mantequilla de
        # marañón" en un plato que ya solo lleva durazno.
        named: list[MealEntry] = []
        for meal in current.meals:
            note = notes.get((current.day_index, meal.slot))
            if note is None or not note.dish_name or meal.is_free_meal:
                named.append(meal)
                continue
            name = note.dish_name.strip()
            if _names_a_missing_food(name, meal, catalog):
                named.append(meal)
                continue
            named.append(meal.model_copy(update={"dish_name": name}))
            renamed += 1
        result.append(current.model_copy(update={"meals": named}))

    return CritiqueOutcome(days=result, renamed=renamed, applied=applied, rejected=rejected)


def _with_swap(
    day: DayPlan,
    slot: MealSlot,
    out_id: UUID,
    in_id: UUID,
    catalog: dict[UUID, FoodItem],
) -> dict[MealSlot, list[FoodItem]] | None:
    """Los alimentos del día con el cambio hecho. None si no se puede."""
    if out_id not in catalog or in_id not in catalog:
        return None
    foods_by_slot: dict[MealSlot, list[FoodItem]] = {}
    swapped = False
    for meal in day.meals:
        if meal.is_free_meal:
            continue
        foods = _foods_of(meal, catalog)
        if meal.slot is slot:
            ids = [f.id for f in foods]
            if out_id not in ids or in_id in ids:
                return None
            foods = [catalog[in_id] if f.id == out_id else f for f in foods]
            swapped = True
        foods_by_slot[meal.slot] = foods
    return foods_by_slot if swapped else None


def _names_a_missing_food(
    name: str, meal: MealEntry, catalog: dict[UUID, FoodItem]
) -> bool:
    """¿El nombre menciona un alimento que la comida ya no lleva?

    Se compara por la palabra más significativa de cada nombre de alimento; es
    aproximado a propósito: descartar un nombre bueno de vez en cuando es más
    barato que prometer un ingrediente que no está.
    """
    present = {
        head
        for i in meal.items
        if i.food_id and i.food_id in catalog
        for head in (_head_word(catalog[i.food_id].name_es),)
    }
    lowered = name.lower()
    for food in catalog.values():
        head = _head_word(food.name_es)
        if len(head) > 4 and head in lowered and head not in present:
            return True
    return False


def _head_word(name: str) -> str:
    """La palabra que de verdad identifica al alimento ("mantequilla de marañón"
    → "marañón"; "pechuga de pollo" → "pollo")."""
    parts = name.lower().split()
    if " de " in name.lower() and len(parts) > 2:
        return parts[-1]
    return parts[0] if parts else ""
