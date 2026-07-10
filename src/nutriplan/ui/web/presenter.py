"""View-models de la UI web: traducción dominio → diseño (Generador Nutricional).

Sin lógica de negocio: solo etiquetas, iconos, colores y porcentajes que las
plantillas Jinja2 pintan tal cual. Los números siempre vienen del dominio.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from nutriplan.adapters.render.view import DAY_LABELS, natural_units
from nutriplan.domain.models import (
    Client,
    DayPlan,
    FoodCategory,
    FoodItem,
    Goal,
    MacroTargets,
    MealSlot,
    NutritionTargets,
    PlanCycle,
    PlanPhase,
    PlanStatus,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import macros_of
from nutriplan.domain.validation import validate_day

DAY_SHORT = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]


def soft_of(hex_color: str, mix: float = 0.87) -> str:
    """Tinte suave de la marca: cada canal mezclado con blanco (87%)."""
    c = (hex_color or "#F26D5B").lstrip("#")
    try:
        r, g, b = (int(c[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        r, g, b = 242, 109, 91
    return "#" + "".join(f"{round(x + (255 - x) * mix):02X}" for x in (r, g, b))


def initials(name: str) -> str:
    parts = [w for w in name.strip().split() if w]
    return "".join(w[0] for w in parts[:2]).upper() or "?"


BRAND_SWATCHES = [
    ("Coral", "#F26D5B"),
    ("Durazno", "#F0925E"),
    ("Fresa", "#EC5F6E"),
    ("Rosa", "#EC6A8E"),
    ("Verde", "#57B98A"),
    ("Lila", "#9B7BD4"),
]

SLOT_META: dict[MealSlot, dict[str, str]] = {
    MealSlot.BREAKFAST: {"name": "Desayuno", "time": "7:00 am", "icon": "wb_sunny"},
    MealSlot.SNACK_AM: {"name": "Snack AM", "time": "10:30 am", "icon": "nutrition"},
    MealSlot.LUNCH: {"name": "Almuerzo", "time": "1:00 pm", "icon": "restaurant"},
    MealSlot.SNACK_PM: {"name": "Snack PM", "time": "4:30 pm", "icon": "nutrition"},
    MealSlot.DINNER: {"name": "Cena", "time": "7:30 pm", "icon": "dinner_dining"},
}

MACRO_META = [
    {"key": "kcal", "label": "Calorías", "unit": "kcal", "color": "#F2704F",
     "soft": "#FCE9E3", "icon": "local_fire_department"},
    {"key": "protein_g", "label": "Proteína", "unit": "g", "color": "#E8607A",
     "soft": "#FBE5EC", "icon": "egg_alt"},
    {"key": "carb_g", "label": "Carbos", "unit": "g", "color": "#E0A537",
     "soft": "#FBF0DA", "icon": "bakery_dining"},
    {"key": "fat_g", "label": "Grasas", "unit": "g", "color": "#B08968",
     "soft": "#F3EBE3", "icon": "water_drop"},
]

GOAL_META: dict[Goal, dict[str, str]] = {
    Goal.LOSE_FAT: {"label": "Déficit", "icon": "trending_down"},
    Goal.MAINTAIN: {"label": "Recomposición", "icon": "trending_flat"},
    Goal.GAIN_MUSCLE: {"label": "Superávit", "icon": "trending_up"},
}

ACTIVITY_LABELS = {
    "sedentary": "Sedentario",
    "light": "Ligero (1–3 días/sem)",
    "moderate": "Moderado (3–5 días/sem)",
    "active": "Activo (6–7 días/sem)",
    "very_active": "Muy activo (2 sesiones/día)",
}

SEX_LABELS = {"female": "Mujer", "male": "Hombre"}

# Toggles de restricciones del diseño. `inverted=True` = el toggle encendido
# significa SIN la restricción ("Con malteada" ON → la whey está permitida).
RESTRICTION_TOGGLES = [
    {"key": "no_seafood", "label": "Sin mariscos", "icon": "set_meal", "inverted": False},
    {"key": "no_dairy", "label": "Sin lácteos", "icon": "icecream", "inverted": False},
    {"key": "no_shake", "label": "Con malteada", "icon": "blender", "inverted": True},
    {"key": "no_gluten", "label": "Sin gluten", "icon": "bakery_dining", "inverted": False},
]

FOOD_GROUPS = [
    {"cat": FoodCategory.PROTEIN, "label": "Proteínas", "icon": "egg_alt"},
    {"cat": FoodCategory.CARB, "label": "Carbohidratos", "icon": "bakery_dining"},
    {"cat": FoodCategory.FAT, "label": "Grasas", "icon": "water_drop"},
    {"cat": FoodCategory.FRUIT, "label": "Frutas", "icon": "nutrition"},
    {"cat": FoodCategory.DAIRY, "label": "Lácteos", "icon": "icecream"},
    {"cat": FoodCategory.VEGETABLE, "label": "Verduras", "icon": "eco"},
]

STATUS_META = {
    "listo": {"label": "Plan listo", "icon": "check_circle", "color": "#2E9E6E",
              "soft": "#E3F4EA"},
    "revision": {"label": "En revisión", "icon": "hourglass_top", "color": "#C9852E",
                 "soft": "#FBF0DA"},
    "sin_plan": {"label": "Sin plan", "icon": "add_circle", "color": "#B0968C",
                 "soft": "#F5EEEA"},
}

AVATAR_PALETTE = [
    ("#FBE0D8", "#D9603F"),
    ("#FBEFD8", "#C9942E"),
    ("#F0E6FB", "#8A63C9"),
    ("#DDEFE6", "#3F9E76"),
    ("#FBE0E8", "#D9527E"),
    ("#E0ECFB", "#3F72B0"),
]

LOADING_MSGS = [
    "Cocinando el plan de {name}…",
    "Balanceando los macros del día…",
    "Eligiendo sus alimentos favoritos…",
    "Dando los últimos toques ✨",
]


def time_ago(dt: object) -> str:
    from datetime import UTC, datetime

    if not isinstance(dt, datetime):
        return ""
    delta = datetime.now(UTC) - dt
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "hace un momento"
    if minutes < 60:
        return f"hace {minutes} min"
    hours = minutes // 60
    if hours < 24:
        return f"hace {hours} h"
    return f"hace {hours // 24} día(s)"


def avatar_colors(client_id: UUID) -> tuple[str, str]:
    return AVATAR_PALETTE[client_id.int % len(AVATAR_PALETTE)]


def fmt_g(value: float) -> str:
    return f"{value:g}" if value < 1000 else f"{value:,.0f}".replace(",", ".")


def fmt_kcal(value: float) -> str:
    return f"{value:,.0f}".replace(",", ".")


def macro_tiles(daily: MacroTargets) -> list[dict[str, Any]]:
    values = daily.model_dump()
    return [
        {**m,
         "value": fmt_kcal(values[m["key"]]) if m["key"] == "kcal"
         else fmt_g(round(values[m["key"]])),
         "raw": round(values[m["key"]])}
        for m in MACRO_META
    ]


def formula_view(client: Client, targets: NutritionTargets) -> dict[str, Any]:
    """g/kg vigentes (de la fórmula o derivados del resultado) + reparto %."""
    w = client.weight_kg or 1.0
    f = targets.formula
    ppk = f.protein_g_per_kg if f.protein_g_per_kg is not None else targets.daily.protein_g / w
    fpk = f.fat_g_per_kg if f.fat_g_per_kg is not None else targets.daily.fat_g / w
    p_kcal = targets.daily.protein_g * 4
    c_kcal = targets.daily.carb_g * 4
    f_kcal = targets.daily.fat_g * 9
    total = max(p_kcal + c_kcal + f_kcal, 1.0)
    return {
        "protein_g_per_kg": round(ppk, 2),
        "fat_g_per_kg": round(fpk, 2),
        "kcal": round(targets.daily.kcal),
        "kcal_manual": f.kcal_override is not None,
        "pct": {
            "protein": round(p_kcal / total * 100),
            "carb": round(c_kcal / total * 100),
            "fat": round(f_kcal / total * 100),
        },
    }


def portion_chip(grams: float, food: FoodItem) -> str:
    g = int(grams) if float(grams).is_integer() else grams
    text = f"{food.name_es.capitalize()} · {g} g"
    if units := natural_units(grams, food):
        text += f" ({units})"
    return text


# El cálculo vive en el dominio; el presenter solo lo reexporta para las rutas.
compute_meal_macros = macros_of


@dataclass
class MealView:
    slot: MealSlot
    name: str
    time: str
    icon: str
    kcal: str
    chips: list[str]
    extras: list[str]
    portions: list[dict[str, Any]]  # para el modo edición: food_id, nombre, gramos


@dataclass
class DayView:
    index: int
    label: str
    meals: list[MealView]
    bars: list[dict[str, Any]]
    fits: bool


def day_view(
    day: DayPlan,
    targets: NutritionTargets,
    config: NutritionConfig,
    foods: dict[UUID, FoodItem],
) -> DayView:
    meals: list[MealView] = []
    for meal in sorted(day.meals, key=lambda m: list(MealSlot).index(m.slot)):
        meta = SLOT_META[meal.slot]
        chips, portions = [], []
        for p in meal.portions:
            food = foods[p.food_id]
            chips.append(portion_chip(p.grams, food))
            portions.append(
                {"food_id": str(p.food_id), "name": food.name_es.capitalize(),
                 "grams": int(p.grams) if float(p.grams).is_integer() else p.grams}
            )
        extras = []
        if meal.free_protein:
            extras.append("Proteína libre")
        if meal.free_salad:
            extras.append("Ensalada libre")
        meals.append(
            MealView(slot=meal.slot, name=meta["name"], time=meta["time"], icon=meta["icon"],
                     kcal=fmt_kcal(meal.computed.kcal), chips=chips, extras=extras,
                     portions=portions)
        )

    totals, daily = day.totals.model_dump(), targets.daily.model_dump()
    bars = []
    for m in MACRO_META:
        actual, target = totals[m["key"]], daily[m["key"]]
        pct = min(actual / target, 1.0) * 100 if target > 0 else 0
        val = fmt_kcal(actual) if m["key"] == "kcal" else f"{fmt_g(round(actual))} g"
        bars.append({**m, "val": val, "pct": round(pct, 1)})

    fits = not validate_day(list(day.meals), targets.daily, config)
    return DayView(index=day.day_index, label=DAY_LABELS[day.day_index], meals=meals,
                   bars=bars, fits=fits)


def plan_pair(cycles: list[PlanCycle]) -> tuple[PlanCycle, PlanCycle] | None:
    """El par (first_15, next_15) más reciente que comparte input_hash."""
    for cycle in cycles:  # list_for_client ya viene DESC por created_at
        if cycle.phase == PlanPhase.FIRST_15:
            sibling = next(
                (c for c in cycles
                 if c.phase == PlanPhase.NEXT_15 and c.input_hash == cycle.input_hash),
                None,
            )
            if sibling:
                return cycle, sibling
    return None


def grid30(
    pair: tuple[PlanCycle, PlanCycle],
    targets: NutritionTargets,
    config: NutritionConfig,
) -> list[dict[str, Any]]:
    """Los 30 días del diseño: día n → ciclo (1–15 / 16–30), day_index (n-1)%7."""
    cells = []
    for n in range(1, 31):
        cycle = pair[0] if n <= 15 else pair[1]
        day = next(d for d in cycle.days if d.day_index == (n - 1) % 7)
        ok = not validate_day(list(day.meals), targets.daily, config)
        cells.append({
            "n": n, "cycle_id": str(cycle.id), "day_index": day.day_index, "fit": ok,
            "color": "#45B37E" if ok else "#E0982E",
            "soft": "#E7F5EE" if ok else "#FBF0DA",
        })
    return cells


def adherence(
    pair: tuple[PlanCycle, PlanCycle], targets: NutritionTargets
) -> list[dict[str, Any]]:
    """Cumplimiento promedio por macro (real/objetivo) sobre los 14 días patrón."""
    days = [d for c in pair for d in c.days]
    daily = targets.daily.model_dump()
    rows = []
    for m in MACRO_META:
        target = daily[m["key"]]
        avg = sum(d.totals.model_dump()[m["key"]] for d in days) / max(len(days), 1)
        pct = avg / target * 100 if target > 0 else 0
        rows.append({**m, "pct": f"{pct:.0f}%", "w": f"{min(pct, 100):.0f}%"})
    return rows


def client_status(cycles: list[PlanCycle]) -> dict[str, str]:
    if not cycles:
        return STATUS_META["sin_plan"]
    if any(c.status == PlanStatus.APPROVED for c in cycles):
        return STATUS_META["listo"]
    return STATUS_META["revision"]


def client_card(client: Client, cycles: list[PlanCycle],
                targets: NutritionTargets | None) -> dict[str, Any]:
    bg, fg = avatar_colors(client.id)
    goal = GOAL_META[client.goal]
    return {
        "id": str(client.id),
        "name": client.name,
        "initials": initials(client.name),
        "avatar_bg": bg,
        "avatar_fg": fg,
        "goal": goal["label"],
        "goal_icon": goal["icon"],
        "kcal": f"{fmt_kcal(targets.daily.kcal)} kcal" if targets else "Sin plan",
        "status": client_status(cycles),
    }
