"""View-models de la UI web: traducción dominio → diseño (Generador Nutricional).

Sin lógica de negocio: solo etiquetas, iconos, colores y porcentajes que las
plantillas Jinja2 pintan tal cual. Los números siempre vienen del dominio.
"""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from nutriplan.adapters.render.color import MACRO_COLORS
from nutriplan.adapters.render.view import DAY_LABELS, natural_units, portion_text
from nutriplan.domain.meal_template import MealCatalog, expand, pool_health
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
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import (
    FAT_G_PER_KG_RANGE,
    PROTEIN_G_PER_KG_RANGE,
    NutritionConfig,
)
from nutriplan.domain.portioning import fits_protein, macros_of
from nutriplan.domain.validation import fiber_shortfall, validate_day

DAY_SHORT = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]

PHASE_LABELS: dict[PlanPhase, str] = {
    PlanPhase.FIRST_15: "Semana 1",
    PlanPhase.NEXT_15: "Semana 2",
}


def plan_phases(cycle: PlanCycle) -> list[PlanPhase]:
    from nutriplan.adapters.render.view import plan_phases_in

    return plan_phases_in(cycle)


def parse_plan_phase(raw: str | None) -> PlanPhase:
    if not raw:
        return PlanPhase.FIRST_15
    try:
        return PlanPhase(raw)
    except ValueError:
        return PlanPhase.FIRST_15


def days_in_phase(cycle: PlanCycle, phase: PlanPhase) -> list[DayPlan]:
    return sorted(
        [d for d in cycle.days if d.phase is phase],
        key=lambda d: d.day_index,
    )


def get_plan_day(cycle: PlanCycle, phase: PlanPhase, day_index: int) -> DayPlan | None:
    for d in cycle.days:
        if d.phase is phase and d.day_index == day_index:
            return d
    return None


def duration_label(cycle: PlanCycle) -> str:
    if cycle.duration_days >= 30:
        return "30 días · 2 semanas"
    return "15 días · 1 semana"


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

# La paleta la manda `adapters.render.color`, que también la usa el PDF: si vive
# solo aquí, el PDF no puede pintar los colores por macro sin que un adapter
# importe de la UI. El icono sí es de la web (Material Symbols).
_MACRO_ICONS = {
    "kcal": "local_fire_department",
    "protein_g": "egg_alt",
    "carb_g": "bakery_dining",
    "fat_g": "water_drop",
}
MACRO_META = [
    {"key": key, "icon": _MACRO_ICONS[key], **meta} for key, meta in MACRO_COLORS.items()
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


def formula_view(
    client: Client, targets: NutritionTargets, *, error: str | None = None
) -> dict[str, Any]:
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
        "protein_range": PROTEIN_G_PER_KG_RANGE,
        "fat_range": FAT_G_PER_KG_RANGE,
        "kcal": round(targets.daily.kcal),
        "kcal_manual": f.kcal_override is not None,
        "error": error,
        "pct": {
            "protein": round(p_kcal / total * 100),
            "carb": round(c_kcal / total * 100),
            "fat": round(f_kcal / total * 100),
        },
    }


def portion_chip(grams: float, food: FoodItem) -> str:
    return portion_text(grams, food)


def portion_edit_fields(
    grams: float,
    food: FoodItem,
    *,
    slot: MealSlot,
    swap_pool: list[FoodItem] | None = None,
) -> dict[str, Any]:
    """Metadatos para el input en línea: unidades naturales + paso de la rejilla."""
    from nutriplan.domain import meal_affinity

    g = int(grams) if float(grams).is_integer() else grams
    units = natural_units(grams, food)
    if food.unit_granularity is not UnitGranularity.GRAMS and food.default_unit_g:
        unit = food.unit_name or "unidad"
        unit_hint = f"≈ {units}" if units else f"{food.default_unit_g:.0f} g por {unit}"
    else:
        unit_hint = "gramos"
    swap_options: list[dict[str, str]] = []
    if swap_pool:
        swap_options = [
            {"id": str(f.id), "name": f.name_es.capitalize()}
            for f in sorted(swap_pool, key=lambda x: x.name_es)
            if f.id != food.id
            and f.category == food.category
            and meal_affinity.allows(f, slot)
        ]
    return {
        "food_id": str(food.id),
        "name": food.name_es.capitalize(),
        "display": portion_text(grams, food),
        "grams": g,
        "step": food.portion_step_g,
        "unit_hint": unit_hint,
        "swap_options": swap_options,
    }


# El cálculo vive en el dominio; el presenter solo lo reexporta para las rutas.
compute_meal_macros = macros_of


@dataclass
class MealView:
    slot: MealSlot
    name: str
    time: str
    icon: str
    kcal: str
    macros: str
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
    fiber_note: str | None = None  # aviso, no error: ver validation.fiber_shortfall


def day_view(
    day: DayPlan,
    targets: NutritionTargets,
    config: NutritionConfig,
    foods: dict[UUID, FoodItem],
    *,
    swap_pool: list[FoodItem] | None = None,
) -> DayView:
    meals: list[MealView] = []
    for meal in sorted(day.meals, key=lambda m: list(MealSlot).index(m.slot)):
        meta = SLOT_META[meal.slot]
        chips, portions = [], []
        for p in meal.portions:
            food = foods[p.food_id]
            chips.append(portion_chip(p.grams, food))
            portions.append(
                portion_edit_fields(
                    p.grams, food, slot=meal.slot, swap_pool=swap_pool
                )
            )
        extras = []
        if meal.free_protein:
            extras.append("Proteína libre")
        if meal.free_salad:
            extras.append("Ensalada libre")
        mc = meal.computed
        macro_str = f"P {round(mc.protein_g)} · C {round(mc.carb_g)} · G {round(mc.fat_g)}"
        meals.append(
            MealView(slot=meal.slot, name=meta["name"], time=meta["time"], icon=meta["icon"],
                     kcal=fmt_kcal(meal.computed.kcal), macros=macro_str,
                     chips=chips, extras=extras, portions=portions)
        )

    totals, daily = day.totals.model_dump(), targets.daily.model_dump()
    bars = []
    for m in MACRO_META:
        actual, target = totals[m["key"]], daily[m["key"]]
        pct = min(actual / target, 1.0) * 100 if target > 0 else 0
        val = fmt_kcal(actual) if m["key"] == "kcal" else f"{fmt_g(round(actual))} g"
        bars.append({**m, "val": val, "pct": round(pct, 1)})

    fits = not validate_day(list(day.meals), targets.daily, config)
    # La fibra informa, no bloquea: el día puede cuadrar de macros y aun así
    # quedarse corto de fibra si al cliente no le gustan las fuentes que la traen.
    short = fiber_shortfall(list(day.meals), targets.daily)
    note = (
        f"Fibra {fmt_g(round(day.totals.fiber_g))} g de "
        f"{fmt_g(round(targets.daily.fiber_g))} g — faltan {fmt_g(short)} g. "
        f"Añade avena, pan integral, frutos secos o legumbres."
        if short
        else None
    )
    return DayView(index=day.day_index, label=DAY_LABELS[day.day_index], meals=meals,
                   bars=bars, fits=fits, fiber_note=note)


def latest_plan(cycles: list[PlanCycle]) -> PlanCycle | None:
    """El plan semanal más reciente del cliente (list_for_client viene DESC)."""
    return cycles[0] if cycles else None


def week_grid(
    cycle: PlanCycle,
    targets: NutritionTargets,
    config: NutritionConfig,
    phase: PlanPhase = PlanPhase.FIRST_15,
) -> list[dict[str, Any]]:
    """Los 7 días de una fase: cada celda marca si el día cuadra."""
    cells = []
    for day in days_in_phase(cycle, phase):
        ok = not validate_day(list(day.meals), targets.daily, config)
        cells.append({
            "n": day.day_index + 1,
            "cycle_id": str(cycle.id),
            "day_index": day.day_index,
            "fase": phase.value,
            "label": DAY_SHORT[day.day_index],
            "fit": ok,
            "color": "#45B37E" if ok else "#E0982E",
            "soft": "#E7F5EE" if ok else "#FBF0DA",
        })
    return cells


def adherence(
    cycle: PlanCycle,
    targets: NutritionTargets,
    phase: PlanPhase = PlanPhase.FIRST_15,
) -> list[dict[str, Any]]:
    """Cumplimiento promedio por macro (real/objetivo) sobre los días de la fase."""
    days = days_in_phase(cycle, phase)
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


def pool_warnings(
    allowed: list[FoodItem],
    catalog: MealCatalog,
    daily: MacroTargets,
    config: NutritionConfig,
) -> list[dict[str, Any]]:
    """Los slots donde el cliente tiene tan pocos platos que va a repetir.

    El universo del plan sigue siendo lo que el cliente marcó que le gusta, así
    que una lista corta condena a repetir. Esto lo hace VISIBLE en el generador —
    antes de gastar una generación— en vez de entregar yogur siete días en
    silencio, que es exactamente lo que pasaba.

    No vive en el plan: es una propiedad de la LISTA DEL CLIENTE, no del plan. Si
    el entrenador añade tres lácteos, el aviso tiene que desaparecer sin
    regenerar nada.
    """
    if not allowed:
        return []
    share = dict(config.meal_distribution)
    targets = {slot: daily.protein_g * share[slot] for slot in MealSlot}

    def admissible(food: FoodItem, slot: MealSlot) -> bool:
        return fits_protein(food, targets[slot])

    pools = expand(catalog, allowed, admissible=admissible)
    out: list[dict[str, Any]] = []
    for warning in pool_health(pools):
        meta = SLOT_META[warning.slot]
        if warning.dish_count == 0:
            message = (
                f"No hay ninguna comida que se pueda armar para {meta['name'].lower()} "
                f"con los alimentos marcados."
            )
        else:
            message = (
                f"Solo hay {warning.dish_count} "
                f"{'opción' if warning.dish_count == 1 else 'opciones'} de "
                f"{meta['name'].lower()}: se van a repetir. Marca más alimentos."
            )
        out.append({
            "slot": warning.slot.value,
            "label": meta["name"],
            "icon": meta["icon"],
            "count": warning.dish_count,
            "message": message,
            "severity": "error" if warning.dish_count == 0 else "warn",
        })
    return out
