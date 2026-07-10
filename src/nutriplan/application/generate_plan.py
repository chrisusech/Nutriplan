"""Generación del plan (Módulo 4): bucle propuesta → cálculo → validación → reintento.

La IA propone QUÉ alimentos (schema con enum de ids permitidos); el código
calcula CUÁNTO (portion solver), valida contra tolerancias y reintenta con
retroalimentación. Tras max_retries sin cuadrar → GenerationError: el caso va
a revisión humana, nunca se entrega un plan fuera de tolerancia sin marca.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import structlog

from nutriplan.adapters.llm.prompts import load_prompt
from nutriplan.domain.errors import GenerationError
from nutriplan.domain.food_filter import allowed_foods
from nutriplan.domain.generation_rules import (
    SLOT_STRUCTURE,
    check_variety,
    validate_selection_structure,
)
from nutriplan.domain.models import (
    Client,
    DayPlan,
    FoodItem,
    MealEntry,
    MealSlot,
    NutritionTargets,
    PlanCycle,
    PlanPhase,
    PlanSelection,
    PlanStatus,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import solve_day_portions
from nutriplan.domain.selection_schema import build_selection_schema
from nutriplan.domain.validation import day_totals, validate_day
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.repository import PlanRepository

logger = structlog.get_logger(__name__)

_SLOT_ORDER = list(MealSlot)

# El plan es UNA semana de 7 días variados (no 15+15). Internamente el ciclo
# conserva phase=FIRST_15 por compatibilidad del schema; el producto ve 1 semana.
WEEK_HINT = "Plan semanal: 7 días variados e intercambiables (cada día cuadra por sí solo)."


def compute_input_hash(
    client: Client,
    targets: NutritionTargets,
    config_version: str,
    prompt_version: str,
    allowed: list[FoodItem],
) -> str:
    """Idempotencia (11.6): mismo insumo → mismo hash → mismo plan."""
    snapshot = {
        "client": {
            "sex": client.sex.value,
            "age": client.age_years,
            "birthdate": client.birthdate.isoformat() if client.birthdate else None,
            "height_cm": client.height_cm,
            "weight_kg": client.weight_kg,
            "goal": client.goal.value,
            "activity": client.activity_level.value,
            "restrictions": sorted(client.restrictions),
        },
        "targets": {
            "daily": targets.daily.model_dump(),
            "overrides": targets.overrides,
        },
        "config_version": config_version,
        "prompt_version": prompt_version,
        "allowed_food_ids": sorted(str(f.id) for f in allowed),
    }
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_selection_prompt(
    targets: NutritionTargets,
    allowed: list[FoodItem],
    config: NutritionConfig,
    feedback: str | None = None,
) -> str:
    structure_lines = [
        f"- {slot.value}: {SLOT_STRUCTURE[slot].description} "
        f"(máx. {SLOT_STRUCTURE[slot].max_items} alimentos)"
        for slot in _SLOT_ORDER
    ]
    catalog_lines = [
        f"- {f.id} | {f.name_es} | {f.category.value}"
        for f in sorted(allowed, key=lambda f: (f.category.value, f.name_es))
    ]
    gen = config.generation
    parts = [
        WEEK_HINT,
        "",
        "ESTRUCTURA DE CADA COMIDA:",
        *structure_lines,
        "",
        "VARIEDAD (obligatoria en almuerzo y cena):",
        f"- La misma proteína máximo {gen.max_protein_repeats_per_week} veces por semana "
        "en el mismo slot.",
        f"- El mismo carbohidrato máximo {gen.max_carb_repeats_per_week} veces por semana "
        "en el mismo slot.",
        "",
        "GUÍA: el almuerzo lleva el carbohidrato principal del día; la cena, uno menor "
        "o solo proteína con ensalada. Desayuno y snacks pueden repetirse entre días.",
        "",
        "CATÁLOGO PERMITIDO (usa exclusivamente estos food_id):",
        *catalog_lines,
    ]
    if feedback:
        parts += ["", "CORRECCIÓN REQUERIDA (la selección anterior falló):", feedback]
    return "\n".join(parts)


async def generate_cycle(
    *,
    client: Client,
    targets: NutritionTargets,
    allowed: list[FoodItem],
    config: NutritionConfig,
    llm: LLMClient,
    prompts_dir: Path,
    model: str,
) -> PlanCycle:
    prompt = load_prompt(prompts_dir, "plan_generation")
    schema = build_selection_schema(allowed)
    foods_by_id = {str(f.id): f for f in allowed}
    input_hash = compute_input_hash(
        client, targets, config.version, prompt.version, allowed
    )

    feedback: str | None = None
    failures: list[str] = []
    for attempt in range(1 + config.generation.max_retries):
        user_prompt = build_selection_prompt(targets, allowed, config, feedback)
        raw = await llm.select_plan(
            system=prompt.text, prompt=user_prompt, schema=schema, model=model
        )
        # normalizar al tipo de dominio (el schema dinámico es estructuralmente idéntico)
        selection = PlanSelection.model_validate(raw.model_dump())

        problems: list[str] = []

        structure = validate_selection_structure(selection, foods_by_id)
        problems += [f"día {v.day_index}, {v.slot.value}: {v.reason}" for v in structure]

        variety = check_variety(
            selection,
            foods_by_id,
            max_protein_repeats=config.generation.max_protein_repeats_per_week,
            max_carb_repeats=config.generation.max_carb_repeats_per_week,
        )
        problems += [
            f"variedad: {v.food_name} usado {v.times_used} veces (máx. {v.limit})"
            for v in variety
        ]

        days: list[DayPlan] = []
        if not problems:
            for day_sel in sorted(selection.days, key=lambda d: d.day_index):
                meals_input = [
                    (m.slot, [foods_by_id[fid] for fid in m.food_ids])
                    for m in sorted(day_sel.meals, key=lambda m: _SLOT_ORDER.index(m.slot))
                ]
                try:
                    solved = solve_day_portions(meals_input, targets.daily, config)
                except GenerationError as exc:
                    problems.append(f"día {day_sel.day_index}: {exc}")
                    continue
                deviations = validate_day(solved, targets.daily, config)
                if deviations:
                    problems += [f"día {day_sel.day_index}, {d}" for d in deviations]
                    continue

                free_salad_of = {m.slot: m.free_salad for m in day_sel.meals}
                days.append(
                    DayPlan(
                        day_index=day_sel.day_index,
                        meals=[
                            MealEntry(
                                slot=m.slot,
                                portions=m.portions,
                                computed=m.computed,
                                free_salad=(
                                    free_salad_of.get(m.slot, False)
                                    or SLOT_STRUCTURE[m.slot].free_salad_default
                                ),
                            )
                            for m in solved
                        ],
                        totals=day_totals(solved),
                    )
                )

        if not problems and len(days) == 7:
            logger.info(
                "plan_generated",
                attempts=attempt + 1,
                input_hash=input_hash[:12],
            )
            return PlanCycle(
                id=uuid4(),
                tenant_id=client.tenant_id,
                client_id=client.id,
                targets_id=targets.id,
                phase=PlanPhase.FIRST_15,  # vestigial: el plan es una semana
                days=days,
                status=PlanStatus.DRAFT,
                config_version=config.version,
                prompt_version=prompt.version,
                model=model,
                input_hash=input_hash,
                created_at=datetime.now(UTC),
            )

        failures = problems
        feedback = "\n".join(f"- {p}" for p in problems[:20])
        logger.warning("plan_retry", attempt=attempt + 1, problems=len(problems))

    raise GenerationError(
        f"No se logró cuadrar la semana tras "
        f"{1 + config.generation.max_retries} intentos. Detalle: "
        + "; ".join(failures[:10])
    )


async def generate_plan_for_client(
    *,
    client: Client,
    targets: NutritionTargets,
    food_repo: FoodRepository,
    plan_repo: PlanRepository,
    config: NutritionConfig,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
) -> PlanCycle:
    """Orquesta el plan semanal: UN ciclo de 7 días variados.

    Idempotente (11.6): si ya existe un plan con el mismo input_hash se devuelve
    sin regenerar (misma entrada → mismo plan, sin llamar a la IA). Sin `llm`
    (modo offline) usa el HeuristicSelector determinista como motor principal.
    """
    liked = await food_repo.get_by_ids(client.liked_food_ids)
    allowed = allowed_foods(liked, client.restrictions)
    if not allowed:
        raise GenerationError(
            "El conjunto permitido quedó vacío: revisa alimentos que le gustan "
            "y restricciones del cliente."
        )

    prompt = load_prompt(prompts_dir, "plan_generation")
    input_hash = compute_input_hash(client, targets, config.version, prompt.version, allowed)
    existing = await plan_repo.find_by_input_hash(input_hash)
    if existing:
        logger.info("plan_reused_by_hash", input_hash=input_hash[:12])
        return existing[0]

    if llm is None:
        from nutriplan.adapters.llm.heuristic import HeuristicSelector

        selector: LLMClient = HeuristicSelector(allowed, targets.daily.protein_g)
    else:
        selector = llm

    cycle = await generate_cycle(
        client=client,
        targets=targets,
        allowed=allowed,
        config=config,
        llm=selector,
        prompts_dir=prompts_dir,
        model=model,
    )
    await plan_repo.add(cycle)
    return cycle
