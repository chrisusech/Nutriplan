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

# Los nombres internos de `application.analytics.Event` dichos en cristiano: la
# consola la lee una persona, no un panel de telemetría.
EVENT_LABELS = {
    "consent_given": "Aceptaron compartir sus datos",
    "onboarding_done": "Terminaron de crear su perfil",
    "menu_generated": "Generaron un menú",
    "dish_rated": "Calificaron un plato",
    "feedback_sent": "Nos escribieron algo",
    "weight_checkin": "Registraron su peso de la semana",
}

FEEDBACK_LABELS = {
    "bug": "Algo falla",
    "idea": "Idea",
    "receta": "Sobre una receta",
    "general": "General",
}

SEX_META = [
    {"key": "female", "label": "Mujer", "icon": "female"},
    {"key": "male", "label": "Hombre", "icon": "male"},
]

GOAL_META: dict[Goal, dict[str, str]] = {
    Goal.LOSE_FAT: {
        "label": "Déficit",
        "icon": "trending_down",
        "title": "Perder grasa",
        "sub": "Bajas de peso cuidando el músculo que ya tienes.",
    },
    Goal.MAINTAIN: {
        "label": "Recomposición",
        "icon": "trending_flat",
        "title": "Mantener peso",
        "sub": "Te quedas donde estás y cambias la composición.",
    },
    Goal.GAIN_MUSCLE: {
        "label": "Superávit",
        "icon": "trending_up",
        "title": "Ganar músculo",
        "sub": "Subes de peso para entrenar más fuerte.",
    },
}

GOAL_LABELS = {goal.value: meta["title"] for goal, meta in GOAL_META.items()}

# Lo mismo que `ACTIVITY_LABELS` pero partido en dos líneas: en una tarjeta el
# paréntesis del final es justo lo que la persona necesita para decidir.
ACTIVITY_META = [
    {"key": "sedentary", "title": "Sedentario", "sub": "Poco o nada de ejercicio", "icon": "chair"},
    {
        "key": "light",
        "title": "Ligeramente activo",
        "sub": "Ejercicio 1 a 3 días por semana",
        "icon": "directions_walk",
    },
    {
        "key": "moderate",
        "title": "Moderadamente activo",
        "sub": "Ejercicio 3 a 5 días por semana",
        "icon": "directions_run",
    },
    {
        "key": "active",
        "title": "Muy activo",
        "sub": "Ejercicio 6 a 7 días por semana",
        "icon": "fitness_center",
    },
    {
        "key": "very_active",
        "title": "Atleta",
        "sub": "Dos sesiones al día",
        "icon": "sports_martial_arts",
    },
]

# Lo que se pregunta en el paso de contexto. Estaba escrito a mano en la
# plantilla: aquí se ve de un vistazo qué sabe la app de cómo vive quien come.
CONTEXT_TOGGLES = [
    {"key": "cocina", "label": "Me encanta cocinar", "icon": "skillet"},
    {"key": "come_afuera", "label": "Como mucho afuera", "icon": "storefront"},
    {"key": "entrena_noche", "label": "Entreno de noche", "icon": "bedtime"},
    {"key": "come_rapido", "label": "Como rápido", "icon": "bolt"},
]

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
MACRO_META = [{"key": key, "icon": _MACRO_ICONS[key], **meta} for key, meta in MACRO_COLORS.items()]


def initials(name: str) -> str:
    parts = [w for w in name.strip().split() if w]
    return "".join(w[0] for w in parts[:2]).upper() or "?"
