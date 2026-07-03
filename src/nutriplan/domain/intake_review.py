"""Detección determinista de ambigüedades del intake (sección 8.2, paso 4).

La IA no rellena huecos: si falta un campo obligatorio o un valor viene
marcado como aproximado, se agrega a `ambiguities` y el intake queda en
NEEDS_REVIEW para que lo resuelva el humano.
"""

from nutriplan.domain.models import IntakeParsed

REQUIRED_FOR_CALCULATION = ("age_years", "height_cm", "weight_kg")

FIELD_LABELS = {
    "age_years": "edad",
    "height_cm": "estatura",
    "weight_kg": "peso",
}


def detect_ambiguities(parsed: IntakeParsed) -> list[str]:
    ambiguities: list[str] = []
    for field in REQUIRED_FOR_CALCULATION:
        if getattr(parsed, field) is None:
            ambiguities.append(f"falta {FIELD_LABELS[field]}")
    if parsed.weight_is_approximate and parsed.weight_kg is not None:
        ambiguities.append("peso aproximado, confirmar con el cliente")
    if not parsed.goal_raw.strip():
        ambiguities.append("falta el objetivo")
    if not any(
        [
            parsed.liked_foods.proteins,
            parsed.liked_foods.carbs,
            parsed.liked_foods.fats,
            parsed.liked_foods.fruits,
            parsed.liked_foods.vegetables,
        ]
    ):
        ambiguities.append("no se identificaron alimentos que le gusten")
    return ambiguities


def all_liked_food_names(parsed: IntakeParsed) -> list[str]:
    lf = parsed.liked_foods
    return [*lf.proteins, *lf.carbs, *lf.fats, *lf.fruits, *lf.vegetables]
