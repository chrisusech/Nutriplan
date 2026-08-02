"""Las etiquetas que la UI pinta: nombres, iconos y colores por concepto.

Sin lógica. Los view-models viven en `week_view.py`; aquí solo está el
vocabulario con el que se le habla a la persona.

Fue un archivo de 616 líneas mientras existió la consola del entrenador. Al
retirarla, todo lo que quedaba en pie era esto.
"""

from typing import Any

from nutriplan.domain.models import CORE_MEAL_SLOTS, FoodCategory, Goal, MealSlot
from nutriplan.ui.web.format import MACRO_COLORS
from nutriplan.ui.web.week_view import SLOT_META

ACTIVITY_LABELS = {
    "sedentary": "Sedentario",
    "light": "Ligero (1–3 días/sem)",
    "moderate": "Moderado (3–5 días/sem)",
    "active": "Activo (6–7 días/sem)",
    "very_active": "Muy activo (2 sesiones/día)",
}

SEX_LABELS = {"female": "Mujer", "male": "Hombre"}

GOAL_META: dict[Goal, dict[str, str]] = {
    Goal.LOSE_FAT: {"label": "Déficit", "icon": "trending_down"},
    Goal.MAINTAIN: {"label": "Recomposición", "icon": "trending_flat"},
    Goal.GAIN_MUSCLE: {"label": "Superávit", "icon": "trending_up"},
}

# Desayuno, almuerzo y cena no se pueden quitar; los snacks sí.
MEAL_TOGGLES = [
    {
        "slot": slot,
        "label": SLOT_META[slot]["name"],
        "icon": SLOT_META[slot]["icon"],
        "core": slot in CORE_MEAL_SLOTS,
    }
    for slot in MealSlot
]

# `inverted` marca las que se enuncian al revés: la casilla dice "con malteada"
# pero la restricción que guarda es "no_shake".
RESTRICTION_TOGGLES = [
    {"key": "no_seafood", "label": "Sin mariscos", "icon": "set_meal", "inverted": False},
    {"key": "no_dairy", "label": "Sin lácteos", "icon": "icecream", "inverted": False},
    {"key": "no_shake", "label": "Con malteada", "icon": "blender", "inverted": True},
    {"key": "no_gluten", "label": "Sin gluten", "icon": "bakery_dining", "inverted": False},
]

FOOD_GROUPS: list[dict[str, Any]] = [
    {"cat": FoodCategory.PROTEIN, "label": "Proteínas", "icon": "egg_alt"},
    {"cat": FoodCategory.CARB, "label": "Carbohidratos", "icon": "bakery_dining"},
    {"cat": FoodCategory.FAT, "label": "Grasas", "icon": "water_drop"},
    {"cat": FoodCategory.FRUIT, "label": "Frutas", "icon": "nutrition"},
    {"cat": FoodCategory.DAIRY, "label": "Lácteos", "icon": "icecream"},
    {"cat": FoodCategory.VEGETABLE, "label": "Verduras", "icon": "eco"},
]

_MACRO_ICONS = {
    "kcal": "local_fire_department",
    "protein_g": "egg_alt",
    "carb_g": "bakery_dining",
    "fat_g": "water_drop",
}
MACRO_META = [
    {"key": key, "icon": _MACRO_ICONS[key], **meta} for key, meta in MACRO_COLORS.items()
]


def initials(name: str) -> str:
    parts = [w for w in name.strip().split() if w]
    return "".join(w[0] for w in parts[:2]).upper() or "?"
