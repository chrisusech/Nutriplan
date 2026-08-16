"""View-model de "Mi semana": el menú tal como lo lee la persona.

Sin lógica de negocio. Los números vienen del dominio; aquí solo se eligen
palabras, iconos y el orden en que se leen.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from nutriplan.domain.cocina import convierte_a_crudo, gramos_en_crudo
from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.meal_template import is_inedible_title, kitchen_name
from nutriplan.domain.models import (
    DayPlan,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealSlot,
    PlanCycle,
)
from nutriplan.domain.portion_label import natural_units, portion_text
from nutriplan.domain.restaurant import is_eating_out
from nutriplan.domain.week import today_weekday
from nutriplan.ui.web.format import DAY_LABELS, DAY_SHORT, MACRO_COLORS

SLOT_META: dict[MealSlot, dict[str, str]] = {
    MealSlot.BREAKFAST: {"name": "Desayuno", "time": "7:00 am", "icon": "wb_sunny"},
    MealSlot.SNACK_AM: {"name": "Snack de media mañana", "time": "10:30 am", "icon": "nutrition"},
    MealSlot.LUNCH: {"name": "Almuerzo", "time": "1:00 pm", "icon": "restaurant"},
    MealSlot.SNACK_PM: {"name": "Snack de la tarde", "time": "4:30 pm", "icon": "nutrition"},
    MealSlot.DINNER: {"name": "Cena", "time": "7:30 pm", "icon": "dinner_dining"},
}

_SLOT_ORDER = list(MealSlot)


def day_chips(cycle: PlanCycle, selected: int, today: int | None = None) -> list[dict[str, Any]]:
    """Los siete días. `today` se resalta para no tener que buscarlo."""
    if today is None:
        today = today_weekday()
    return [
        {
            "index": day.day_index,
            "short": DAY_SHORT[day.day_index],
            "label": DAY_LABELS[day.day_index],
            "on": day.day_index == selected,
            "is_today": day.day_index == today,
        }
        for day in sorted(cycle.days, key=lambda d: d.day_index)
    ]


def _redondo(grams: float) -> int | float:
    return int(grams) if float(grams).is_integer() else round(grams, 1)


def _portion_parts(grams: float, food: FoodItem) -> dict[str, str]:
    """La porción en dos columnas: qué es y cuánto es.

    Se emiten las dos medidas —la del plato y la de la compra— para que la
    pantalla pueda alternarlas sin volver al servidor. `qty` sigue siendo la
    cocida: es la que cuadra con los macros de al lado.
    """
    unidades = natural_units(grams, food)
    cocido = _redondo(grams)
    crudo = _redondo(gramos_en_crudo(food, grams))
    return {
        "name": food.name_es.capitalize(),
        "qty": f"{unidades} · {cocido} g" if unidades else f"{cocido} g",
        "qty_crudo": f"{unidades} · {crudo} g" if unidades else f"{crudo} g",
        # Vacío cuando comprar y servir pesan lo mismo: el interruptor no tiene
        # nada que enseñar y la línea no lo anuncia.
        "convertible": "1" if convierte_a_crudo(food) else "",
    }


def _pct(value: float, target: float) -> int:
    """Porcentaje redondeado de cinco en cinco, que es como lo pinta el CSS.

    El anillo y las barras no pueden llevar un ancho inline —la CSP lo prohíbe—,
    así que el porcentaje viaja en `data-pct` y la escala vive en la hoja.
    """
    if target <= 0:
        return 0
    return min(100, max(0, round(value / target * 20) * 5))


def day_progress(day: dict[str, Any], target: MacroTargets | None) -> dict[str, Any] | None:
    """Lo ya marcado contra el objetivo del día: el anillo y las tres barras.

    Sin objetivo no hay anillo — es preferible no pintar nada a inventar un
    denominador. El menú planeado sigue en `day["kcal"]`; aquí solo cuenta
    lo que la persona ya comió.
    """
    if target is None:
        return None
    eaten = [m for m in day["meals"] if m.get("eaten")]
    kcal = sum(int(m["kcal"]) for m in eaten)
    objetivo = round(target.kcal)
    ratio = kcal / objetivo if objetivo else 0.0
    meals_n = len(day["meals"])
    eaten_n = len(eaten)
    terminado = meals_n > 0 and eaten_n == meals_n
    if ratio > 1.03:
        estado = "alto"
    elif terminado and ratio < 0.97:
        estado = "bajo"
    else:
        estado = "ok"
    return {
        "kcal": kcal,
        "objetivo": objetivo,
        "restante": max(0, objetivo - kcal),
        "pct": _pct(kcal, objetivo),
        "estado": estado,
        "eaten_n": eaten_n,
        "meals_n": meals_n,
        "macros": [
            {
                "key": key,
                "label": MACRO_COLORS[key]["label"],
                "value": sum(int(m[key]) for m in eaten),
                "target": round(getattr(target, key)),
            }
            for key in ("protein_g", "carb_g", "fat_g")
        ],
    }


def _meal_title(meal: MealEntry, foods: dict[UUID, FoodItem]) -> str:
    """El nombre del plato si lo tiene; si no, sus alimentos.

    El nombre lo pone el motor de platos o el crítico. Cuando falta —modo
    offline sin catálogo— la lista sigue siendo mejor que un hueco.

    Si la plantilla decía "Fruta con crema…" pero el solver solo dejó fruta
    (gramos 0 en la crema), el título guardado miente: se rehace con lo servido.
    """
    names = [
        kitchen_name(foods[i.food_id].name_es)
        for i in meal.items
        if i.food_id and i.food_id in foods
    ]
    if meal.dish_name and not is_inedible_title(meal.dish_name):
        if " con " in meal.dish_name and len(names) == 1:
            return names[0].capitalize()
        return meal.dish_name
    if not names:
        return SLOT_META[meal.slot]["name"]
    return " con ".join([names[0].capitalize(), *names[1:]])


def meal_view(
    meal: MealEntry,
    foods: dict[UUID, FoodItem],
    recipe: DishRecipe | None = None,
    rating: int | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    meta = SLOT_META[meal.slot]
    if is_eating_out(meal):
        qty = meal.dish_name or "1 porción"
        return {
            "slot": meal.slot.value,
            "slot_label": meta["name"],
            "time": meta["time"],
            "icon": "restaurant",
            "title": meal.dish_name or "Comida de calle",
            "is_free": False,
            "is_out": True,
            "kcal": round(meal.computed.kcal),
            "protein_g": round(meal.computed.protein_g),
            "carb_g": round(meal.computed.carb_g),
            "fat_g": round(meal.computed.fat_g),
            "ingredients": [qty],
            "items": [{"name": qty, "qty": "macros del menú"}],
            "steps": [],
            "dish_key": meal.dish_key,
            "prep_minutes": None,
            "difficulty": None,
            "tips": None,
            "rating": rating,
            "comment": comment,
            "eaten": meal.eaten,
        }
    if meal.is_free_meal:
        return {
            "slot": meal.slot.value,
            "slot_label": meta["name"],
            "time": meta["time"],
            "icon": "restaurant",
            "title": "¿Comes fuera?",
            "is_free": False,
            "is_out": False,
            "needs_out": True,
            "kcal": 0,
            "protein_g": 0,
            "carb_g": 0,
            "fat_g": 0,
            "ingredients": [],
            "items": [],
            "steps": [],
            "rating": rating,
            "comment": comment,
            "eaten": meal.eaten,
        }

    servidos = [
        (item.grams, foods[item.food_id])
        for item in meal.items
        if item.food_id and item.grams and item.food_id in foods
    ]
    ingredients = [portion_text(g, food) for g, food in servidos]
    # La misma porción partida en dos: el nombre a la izquierda y la cantidad a
    # la derecha, que es como se lee una comida de un vistazo.
    items = [_portion_parts(g, food) for g, food in servidos]
    for extra in (
        "Ensalada libre" if meal.free_salad else "",
        "Proteína libre" if meal.free_protein else "",
    ):
        if extra:
            ingredients.append(extra)
            items.append({"name": extra, "qty": "a tu gusto"})

    steps: list[str] = []
    if recipe:
        # Receta YAML de plantilla + ingredientes incompletos = mentira en pantalla.
        if recipe.source == "yaml" and not _steps_match_ingredients(recipe.steps, ingredients):
            steps = []
        else:
            steps = list(recipe.steps)

    title = _meal_title(meal, foods)
    if (
        recipe
        and recipe.name_es
        and recipe.source == "ai"
        and not is_inedible_title(recipe.name_es)
    ):
        title = recipe.name_es

    return {
        "slot": meal.slot.value,
        "slot_label": meta["name"],
        "time": meta["time"],
        "icon": meta["icon"],
        "title": title,
        "is_free": False,
        "is_out": False,
        "kcal": round(meal.computed.kcal),
        "protein_g": round(meal.computed.protein_g),
        "carb_g": round(meal.computed.carb_g),
        "fat_g": round(meal.computed.fat_g),
        "ingredients": ingredients,
        "items": items,
        "steps": steps,
        "dish_key": meal.dish_key,
        "prep_minutes": recipe.prep_minutes if recipe and steps else None,
        "difficulty": recipe.difficulty if recipe and steps else None,
        "tips": recipe.tips if recipe and steps else None,
        "rating": rating,
        "comment": comment,
        "eaten": meal.eaten,
    }


def _steps_match_ingredients(steps: list[str], ingredients: list[str]) -> bool:
    """Rechaza pasos que asumen un alimento que no está en el plato."""
    blob = " ".join(ingredients).lower()
    for step in steps:
        lower = step.lower()
        if "crema" in lower or "frutos secos" in lower:
            if not any(
                token in blob
                for token in (
                    "mantequilla",
                    "almendra",
                    "maní",
                    "mani",
                    "marañón",
                    "maranon",
                    "tahini",
                    "nuez",
                    "crema",
                    "frutos secos",
                )
            ):
                return False
        if "loncha" in lower and (
            "loncha" not in blob and "jamón" not in blob and "jamon" not in blob
        ):
            return False
    return True


def day_view(
    day: DayPlan,
    foods: dict[UUID, FoodItem],
    recipes: dict[str, DishRecipe] | None = None,
    ratings: Mapping[str, Mapping[str, object] | int] | None = None,
) -> dict[str, Any]:
    recipes = recipes or {}
    ratings = ratings or {}
    meals = sorted(day.meals, key=lambda m: _SLOT_ORDER.index(m.slot))

    def _rating_bits(slot: str) -> tuple[int | None, str | None]:
        raw = ratings.get(slot)
        if raw is None:
            return None, None
        if isinstance(raw, int):
            return raw, None
        rating_raw = raw.get("rating")
        comment_raw = raw.get("comment")
        return (
            int(rating_raw) if isinstance(rating_raw, int) else None,
            str(comment_raw) if comment_raw else None,
        )

    pending = [m for m in meals if not m.eaten]
    ancla = pending[0] if pending else (meals[-1] if meals else None)
    return {
        "index": day.day_index,
        "label": DAY_LABELS[day.day_index],
        "kcal": round(day.totals.kcal),
        "protein_g": round(day.totals.protein_g),
        "carb_g": round(day.totals.carb_g),
        "fat_g": round(day.totals.fat_g),
        "fuera_slot": ancla.slot.value if ancla else MealSlot.DINNER.value,
        "meals": [
            meal_view(
                m,
                foods,
                recipes.get(m.dish_key or ""),
                *_rating_bits(m.slot.value),
            )
            for m in meals
        ],
    }
