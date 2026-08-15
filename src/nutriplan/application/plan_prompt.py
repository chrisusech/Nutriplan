"""Lo que se le enseña a la IA para que elija los alimentos de la semana.

Un prompt es producto: el catálogo con densidades, la orientación por comida, lo
que la persona ya dijo que le gusta y los platos que otros votaron bien. Se
arma aquí, aparte del bucle de generación, porque se lee y se ajusta mucho más
seguido de lo que se toca el motor.
"""

from uuid import UUID

from nutriplan.domain.generation_rules import SLOT_STRUCTURE
from nutriplan.domain.models import (
    FoodItem,
    MealSlot,
    NutritionTargets,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.proven import ProvenDish
from nutriplan.domain.taste import TasteProfile

_SLOT_ORDER = list(MealSlot)


def slots_of(config: NutritionConfig) -> list[MealSlot]:
    """Las comidas de este cliente: las que su reparto declara, en orden del día.

    El reparto ya viene recortado a lo que el cliente come (`config.for_slots`),
    así que el motor, el schema y el validador leen la misma fuente.
    """
    return [s for s in _SLOT_ORDER if s in config.meal_distribution]


def _catalog_line(food: FoodItem, *, alias: str | None = None) -> str:
    """Línea de catálogo con densidad: la IA elige a ciegas sin esto."""
    ref = alias or str(food.id)
    density = (
        f"P{food.protein_100g:.0f}/C{food.carb_100g:.0f}/G{food.fat_100g:.0f}"
        f"/kcal{food.kcal_100g:.0f} por 100g"
    )
    line = f"- {ref} | {food.name_es} | {food.category.value} | {density}"
    if food.default_unit_g and food.unit_name:
        line += f" | 1 {food.unit_name}≈{food.default_unit_g:.0f}g"
    return line


def _slot_macro_guide(targets: NutritionTargets, config: NutritionConfig) -> list[str]:
    """Orientación por comida con pistas accionables para cuadrar macros."""
    daily = targets.daily
    lines: list[str] = []
    for slot in slots_of(config):
        share = config.meal_distribution[slot]
        kcal = daily.kcal * share.kcal
        prot = daily.protein_g * share.protein_g
        carb = daily.carb_g * share.carb_g
        hints: list[str] = []
        if carb >= 90:
            hints.append(
                f"carbo ALTO (~{carb:.0f}g): incluye fuente densa "
                "(avena, arepa, arroz, plátano, pan, yuca) — no solo huevo/aguacate"
            )
        elif carb >= 50:
            hints.append(f"carbo medio (~{carb:.0f}g): al menos un carbo contable o denso")
        if prot <= 8:
            hints.append("proteína ≈0: fruta o snack dulce — sin huevo, pollo, queso ni atún")
        elif prot >= 35:
            hints.append(f"proteína alta (~{prot:.0f}g): proteína densa, no solo loncha magra")
        hint_txt = f" | {'; '.join(hints)}" if hints else ""
        lines.append(
            f"- {slot.value}: ≈{kcal:.0f} kcal, {prot:.0f} g prot, {carb:.0f} g carb{hint_txt}"
        )
    return lines


def build_selection_prompt(
    targets: NutritionTargets,
    allowed: list[FoodItem],
    config: NutritionConfig,
    feedback: str | None = None,
    *,
    habits: str | None = None,
    dislikes: list[str] | None = None,
    context_tags: list[str] | None = None,
    city: str | None = None,
    aliases: dict[str, UUID] | None = None,
    taste: TasteProfile | None = None,
    proven: list[ProvenDish] | None = None,
    on_hand: list[str] | None = None,
) -> str:
    slots = slots_of(config)
    structure_lines = [
        f"- {slot.value}: {SLOT_STRUCTURE[slot].description} "
        f"(máx. {SLOT_STRUCTURE[slot].max_items} alimentos)"
        for slot in slots
    ]
    id_by_food = {v: k for k, v in aliases.items()} if aliases else {}
    catalog_lines = [
        _catalog_line(
            f,
            alias=id_by_food.get(f.id) if aliases else None,
        )
        for f in sorted(allowed, key=lambda f: (f.category.value, f.name_es))
    ]
    daily = targets.daily
    id_label = "alias (f0, f1…)" if aliases else "food_id"
    parts = [
        "Menú nutricional de una semana: 7 días.",
        "",
        "OBJETIVO DIARIO (lo calcula el código; tú NO inventes gramos ni kcal):",
        f"- {daily.kcal:.0f} kcal | prot {daily.protein_g:.0f} g | "
        f"carb {daily.carb_g:.0f} g | grasa {daily.fat_g:.0f} g",
        "",
        "ORIENTACIÓN POR COMIDA (elige alimentos que puedan cubrir cerca; "
        "el solver corrige porciones):",
        *_slot_macro_guide(targets, config),
        "",
        f"CADA DÍA TIENE {len(slots)} COMIDAS, exactamente estas: "
        f"{', '.join(s.value for s in slots)}.",
        "",
        "ESTRUCTURA DE CADA COMIDA:",
        *structure_lines,
        "",
        "PLATOS DE COCINA REAL (obligatorio — no improvises porciones locas):",
        "- Elige combinaciones que alguien cocinaría y COMERÍA a gusto.",
        "- Pon `dish_name` de plato cocinado (ej. 'Lomo al ajillo con batata'), "
        "nunca 'Proteína con carbohidrato'.",
        "- Carbohidratos contables (tortilla, arepa, rebanada de pan): máximo "
        "3 unidades en desayuno; 2 en almuerzo/cena. Si el plato necesita más "
        "energía, elige arroz, papa, yuca, pasta o plátano — no apiles "
        "tortillas.",
        "- Un plato = proteína + carbohidrato que combine + acompañamiento.",
        "- Evita 1 huevo con 6 panes, 3 lonchas con 9 tortillas, o 400 g de "
        "un solo carbo contable.",
        "",
        "PRIORIDAD: cuadrar la orientación por comida de arriba. Puedes repetir "
        "alimentos entre días si hace falta para que el solver pueda porcionar.",
        "",
        f"CATÁLOGO PERMITIDO (usa exclusivamente estos {id_label}; densidad = por 100 g):",
        *catalog_lines,
    ]
    if city and city.strip():
        parts += ["", f"CIUDAD / REGIÓN: {city.strip()} (prioriza comida local si cabe)."]
    if context_tags:
        parts += ["", "CONTEXTO: " + ", ".join(context_tags)]
    if dislikes:
        parts += [
            "",
            "NO QUIERE VER (respeta aunque esté en el catálogo): " + ", ".join(dislikes),
        ]
    if habits and habits.strip():
        parts += [
            "",
            "HÁBITOS DEL CLIENTE (respeta el estilo; no inventes alimentos fuera del catálogo):",
            habits.strip(),
        ]
    if on_hand:
        parts += [
            "",
            # Es una ventaja, no una obligación: llenarle la semana de lo que
            # tiene en casa sería castigar a quien nos lo cuenta.
            "YA LO TIENE EN CASA (úsalo si encaja; nunca a costa de la variedad): "
            + ", ".join(on_hand),
        ]
    parts += _taste_lines(taste, aliases)
    parts += _proven_lines(proven, aliases)
    if feedback:
        parts += ["", "CORRECCIÓN REQUERIDA (la selección anterior falló):", feedback]
    return "\n".join(parts)


def _taste_lines(taste: TasteProfile | None, aliases: dict[str, UUID] | None) -> list[str]:
    """Lo aprendido de esta persona semana a semana.

    Va después de los hábitos y antes de la corrección del solver: son
    preferencias fuertes, pero nunca mandan más que cuadrar los macros.
    """
    if taste is None or taste.is_empty:
        return []
    alias_by_id = {v: k for k, v in (aliases or {}).items()}

    def _label(food_id: UUID) -> str | None:
        return alias_by_id.get(food_id) if aliases else str(food_id)

    lines = ["", "LO QUE YA SABEMOS DE ESTA PERSONA (semanas anteriores):"]
    if taste.loved_dishes:
        lines.append(
            "- Le encantaron: " + ", ".join(taste.loved_dishes) + ". "
            "Puedes repetir esa idea con otros alimentos del catálogo."
        )
    if taste.rejected_dishes:
        lines.append(
            "- NO le gustaron: " + ", ".join(taste.rejected_dishes) + ". No los vuelvas a proponer."
        )
    avoid = [a for a in (_label(f) for f in taste.avoid_food_ids) if a]
    if avoid:
        lines.append("- Pidió no volver a ver: " + ", ".join(avoid) + ".")
    prefer = [a for a in (_label(f) for f in taste.prefer_food_ids) if a]
    if prefer:
        lines.append("- Pidió más de: " + ", ".join(prefer) + ".")
    if taste.adjustments:
        lines.append("- Pidió además: " + "; ".join(taste.adjustments) + ".")
    return lines


def _proven_lines(proven: list[ProvenDish] | None, aliases: dict[str, UUID] | None) -> list[str]:
    """Platos que otra gente ya calificó bien con estos mismos alimentos.

    Van como sugerencia, no como orden: el objetivo diario sigue mandando y hay
    siete días que llenar. Sin los alias, el modelo vería nombres de comida sin
    saber con qué ids reproducirlos.
    """
    if not proven:
        return []
    alias_by_id = {v: k for k, v in (aliases or {}).items()}
    lines: list[str] = []
    for dish in proven:
        labels = [alias_by_id.get(f) if aliases else str(f) for f in dish.food_ids]
        named = [label for label in labels if label]
        if len(named) != len(dish.food_ids):
            continue
        lines.append(f"- {dish.name_es} ({', '.join(named)}) — {dish.rating_avg:.1f}★")
    if not lines:
        return []
    return [
        "",
        "PLATOS PROBADOS (nota alta con estos alimentos; reutilízalos cuando encajen, no fuerces):",
        *lines,
    ]
