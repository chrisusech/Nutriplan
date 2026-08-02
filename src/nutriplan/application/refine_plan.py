"""La pasada crítica: pedirle sabor a la IA sobre un menú ya cuadrado.

El sistema es exacto en kcal y ciego al gusto. Esto lo compensa sin ceder el
control: la IA ve la semana y propone nombres y sustituciones; `domain.critique`
decide cuáles sobreviven al solver.

Si no hay LLM, o si el proveedor falla, el menú sigue siendo válido. Refinar es
una mejora, nunca un requisito.
"""

from pathlib import Path
from uuid import UUID

import structlog

from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.application.food_pool import shortlist_for_llm
from nutriplan.domain.critique import (
    CritiqueOutcome,
    apply_critique,
    build_critique_schema,
    food_aliases,
)
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import Client, DayPlan, FoodItem, MacroTargets
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.ports.llm_client import LLMClient

logger = structlog.get_logger(__name__)

REVIEW_PROMPT_VERSION = 1
# El relato del onboarding es texto libre de un desconocido: entra delimitado y
# recortado, y su salida está encerrada por el schema.
MAX_HABITS_CHARS = 1200


def _fence(label: str, text: str) -> str:
    """Delimita texto de la persona para que no se lea como instrucción."""
    clean = text.strip().replace("```", "")[:MAX_HABITS_CHARS]
    return f"{label}:\n```\n{clean}\n```"


def build_review_prompt(
    days: list[DayPlan],
    client: Client,
    catalog: dict[UUID, FoodItem],
    aliases: dict[str, UUID] | None = None,
) -> str:
    """La semana tal como quedó, más quién se la va a comer."""
    by_id = {v: k for k, v in (aliases or food_aliases(list(catalog.values()))).items()}
    lines: list[str] = ["MENÚ DE LA SEMANA (ya cuadrado; los gramos son finales):"]
    for day in sorted(days, key=lambda d: d.day_index):
        lines.append(f"\nDía {day.day_index}:")
        for meal in day.meals:
            if meal.is_free_meal:
                lines.append(f"- {meal.slot.value}: COMIDA LIBRE (no la toques)")
                continue
            foods = ", ".join(
                f"{catalog[i.food_id].name_es} [{by_id[i.food_id]}]"
                for i in meal.items
                if i.food_id and i.food_id in by_id
            )
            lines.append(f"- {meal.slot.value}: {foods}")

    lines.append("\nQUIÉN SE LO VA A COMER:")
    if client.city or client.country:
        lines.append(f"- Vive en: {client.city or ''} {client.country or ''}".strip())
    if client.context_tags:
        lines.append(f"- Contexto: {', '.join(client.context_tags)}")
    if client.dislikes:
        lines.append(f"- No quiere ver: {', '.join(client.dislikes)}")
    if client.restrictions:
        lines.append(f"- Restricciones: {', '.join(client.restrictions)}")
    if client.eating_pattern_raw:
        lines.append(_fence("- Cómo come, en sus palabras", client.eating_pattern_raw))

    lines.append("\nALIMENTOS PARA SUSTITUIR (id | nombre | categoría):")
    lines += [
        f"- {by_id[f.id]} | {f.name_es} | {f.category.value}"
        for f in sorted(catalog.values(), key=lambda f: (f.category.value, f.name_es))
        if f.id in by_id
    ]
    return "\n".join(lines)


async def refine_week(
    *,
    days: list[DayPlan],
    client: Client,
    allowed: list[FoodItem],
    config: NutritionConfig,
    daily: MacroTargets,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
) -> CritiqueOutcome | None:
    """Devuelve la semana refinada, o None si no se pudo refinar.

    None no es un fallo: es "el menú se queda como estaba", que es un resultado
    perfectamente bueno.
    """
    if llm is None or not allowed:
        return None

    # El crítico ve la semana entera: enseñarle además los 172 alimentos se
    # come la cuota de tokens por minuto. Le basta con lo que ya está en el
    # menú más un abanico corto para sustituir.
    in_plan = {i.food_id for d in days for m in d.meals for i in m.items if i.food_id}
    shortlist = shortlist_for_llm(allowed)
    catalog = {
        f.id: f for f in allowed if f.id in in_plan or f in shortlist
    }
    prompt = load_prompt(prompts_dir, "plan_review", REVIEW_PROMPT_VERSION)
    aliases = food_aliases(list(catalog.values()))
    schema = build_critique_schema(list(catalog.values()))

    try:
        critique = await llm.extract(
            system=prompt.text,
            text=build_review_prompt(days, client, catalog, aliases),
            schema=schema,
            model=model,
        )
    except LLMError as exc:
        # Que la IA no conteste no puede costarle el menú a nadie.
        logger.warning("plan_review_skipped", error=str(exc))
        return None

    outcome = apply_critique(days, critique, catalog, config, daily, aliases)
    logger.info(
        "plan_reviewed",
        renamed=outcome.renamed,
        swaps_applied=outcome.applied,
        swaps_rejected=len(outcome.rejected),
    )
    return outcome
