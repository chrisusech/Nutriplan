"""De lo que la IA eligió al plan de la semana, con gramos y macros.

Aquí es donde el código recupera el mando: recibe una selección de alimentos,
llama al solver, valida el día contra las tolerancias y descarta lo que no
cuadra. Ni una cifra sale de la IA.
"""

from uuid import UUID

from nutriplan.domain.dish_recipe import dish_key
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.generation_rules import SLOT_STRUCTURE
from nutriplan.domain.macro_split import daily_minus_free_meal, macro_shares
from nutriplan.domain.meal_template import Dish
from nutriplan.domain.models import (
    DayPlan,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
    NutritionTargets,
    PlanSelection,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import solve_day_portions
from nutriplan.domain.validation import day_totals, validate_day

_SLOT_ORDER = list(MealSlot)


def _free_meal_entry(slot: MealSlot) -> MealEntry:
    """La celda libre: sin alimentos, sin gramos y sin macros que contar."""
    return MealEntry(
        slot=slot,
        items=[],
        computed=MacroTargets(kcal=0.0, protein_g=0.0, carb_g=0.0, fat_g=0.0),
        is_free_meal=True,
    )


def _name_from_served(food_ids: list[UUID], foods_by_id: dict[str, FoodItem]) -> str:
    """Nombre honesto a partir de lo que el solver realmente dejó en el plato."""
    names = [foods_by_id[str(fid)].name_es for fid in food_ids if str(fid) in foods_by_id]
    if not names:
        return "Plato"
    head, *rest = names
    if not rest:
        return head.capitalize()
    return f"{head.capitalize()} con " + ", ".join(rest)


def selection_to_days(
    selection: PlanSelection,
    *,
    foods_by_id: dict[str, FoodItem],
    targets: NutritionTargets,
    config: NutritionConfig,
    free_meal: tuple[int, MealSlot] | None = None,
    dishes: dict[tuple[int, MealSlot], Dish] | None = None,
) -> tuple[list[DayPlan], list[str]]:
    problems: list[str] = []
    days: list[DayPlan] = []
    free_salad_of = {(d.day_index, m.slot): m.free_salad for d in selection.days for m in d.meals}
    for day_sel in sorted(selection.days, key=lambda d: d.day_index):
        meals_input = [
            (m.slot, [foods_by_id[fid] for fid in m.food_ids])
            for m in sorted(day_sel.meals, key=lambda m: _SLOT_ORDER.index(m.slot))
        ]
        # El día de la comida libre se porciona y se juzga contra un objetivo MENOR:
        # el suyo menos lo que pesaba esa comida. Así las que quedan conservan su
        # objetivo de siempre y el día suma por debajo — que es lo que significa
        # comerse una pizza. Sin esto, `macro_shares` renormalizaría y las cuatro
        # comidas restantes cargarían con el día entero.
        free_slot = free_meal[1] if free_meal and free_meal[0] == day_sel.day_index else None
        day_daily = daily_minus_free_meal(targets.daily, config, free_slot)
        try:
            solved = solve_day_portions(meals_input, day_daily, config)
        except GenerationError as exc:
            problems.append(f"día {day_sel.day_index}: {exc}")
            continue
        deviations = validate_day(
            solved,
            day_daily,
            config,
            shares=macro_shares(meals_input, config),
        )
        if deviations:
            problems += [f"día {day_sel.day_index}, {d}" for d in deviations]
            continue
        meals = []
        named = {m.slot: m for m in day_sel.meals}
        for m in solved:
            dish = (dishes or {}).get((day_sel.day_index, m.slot))
            food_ids = [p.food_id for p in m.portions]
            template_id = dish.template_id if dish else None
            dish_name = dish.name if dish else None
            # Si el solver dejó un componente en 0 g (p. ej. la crema porque el
            # día ya iba lleno de grasa), el nombre y la receta YAML de la
            # plantilla mienten: "Fruta con crema…" con solo banano. Renombramos
            # por lo servido y quitamos la plantilla para no arrastrar sus pasos.
            if dish is not None and set(food_ids) != set(dish.food_ids):
                dish_name = _name_from_served(food_ids, foods_by_id)
                template_id = None
            named_meal = named.get(m.slot)
            llm_name = (named_meal.dish_name or "").strip() if named_meal is not None else ""
            if llm_name:
                dish_name = llm_name
            meals.append(
                MealEntry(
                    slot=m.slot,
                    template_id=template_id,
                    dish_name=dish_name,
                    dish_key=dish_key(template_id, food_ids) if food_ids else None,
                    items=[
                        MealItem(food_id=p.food_id, grams=p.grams, position=i)
                        for i, p in enumerate(m.portions)
                    ],
                    computed=m.computed,
                    free_salad=(
                        free_salad_of.get((day_sel.day_index, m.slot), False)
                        or SLOT_STRUCTURE[m.slot].free_salad_default
                    ),
                )
            )
        if free_slot is not None:
            meals.append(_free_meal_entry(free_slot))
            meals.sort(key=lambda m: _SLOT_ORDER.index(m.slot))  # el orden del día
        days.append(
            DayPlan(
                day_index=day_sel.day_index,
                meals=meals,
                # La comida libre no suma: los totales del día son los de lo que se
                # pesa, y por eso el día sale por debajo del objetivo. Es la verdad.
                totals=day_totals(solved),
            )
        )
    return days, problems
