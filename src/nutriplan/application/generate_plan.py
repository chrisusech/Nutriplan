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
    slot_availability,
    validate_selection_structure,
)
from nutriplan.domain.meal_template import MealCatalog
from nutriplan.domain.models import (
    Client,
    DayPlan,
    FoodItem,
    MealEntry,
    MealItem,
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
from nutriplan.ports.repository import ClientRepository, PlanRepository

logger = structlog.get_logger(__name__)

_SLOT_ORDER = list(MealSlot)

_PHASE_HINT = {
    PlanPhase.FIRST_15: "FASE 1 (días 1-15): primera semana del ciclo.",
    PlanPhase.NEXT_15: (
        "FASE 2 (días 16-30): segunda semana — menú DISTINTO a la fase 1; "
        "rota proteínas y carbohidratos respecto a la semana anterior."
    ),
}


def compute_input_hash(
    client: Client,
    targets: NutritionTargets,
    config_version: str,
    prompt_version: str,
    allowed: list[FoodItem],
    variant: int = 0,
    *,
    duration_days: int = 15,
    catalog_version: str = "",
) -> str:
    """Idempotencia (11.6): mismo insumo → mismo hash → mismo plan.

    `catalog_version` es la versión del catálogo de PLATOS. Sin ella, editar
    meal_templates.yaml no cambia el hash, `find_by_input_hash` devuelve el plan
    viejo y los platos nuevos no aparecen jamás — parecería que el motor está
    roto cuando lo que está roto es la caché.
    """
    snapshot = {
        "catalog_version": catalog_version,
        "variant": variant,
        "duration_days": duration_days,
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
    *,
    duration_days: int = 15,
    phase: PlanPhase = PlanPhase.FIRST_15,
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
    duration_label = f"{duration_days} días" if duration_days >= 30 else "15 días (1 semana)"
    parts = [
        f"Plan nutricional de {duration_label}.",
        _PHASE_HINT[phase],
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


def _selection_to_days(
    selection: PlanSelection,
    *,
    phase: PlanPhase,
    foods_by_id: dict[str, FoodItem],
    targets: NutritionTargets,
    config: NutritionConfig,
) -> tuple[list[DayPlan], list[str]]:
    problems: list[str] = []
    days: list[DayPlan] = []
    free_salad_of = {
        (d.day_index, m.slot): m.free_salad for d in selection.days for m in d.meals
    }
    for day_sel in sorted(selection.days, key=lambda d: d.day_index):
        meals_input = [
            (m.slot, [foods_by_id[fid] for fid in m.food_ids])
            for m in sorted(day_sel.meals, key=lambda m: _SLOT_ORDER.index(m.slot))
        ]
        try:
            solved = solve_day_portions(meals_input, targets.daily, config)
        except GenerationError as exc:
            problems.append(f"fase {phase.value}, día {day_sel.day_index}: {exc}")
            continue
        deviations = validate_day(solved, targets.daily, config)
        if deviations:
            problems += [
                f"fase {phase.value}, día {day_sel.day_index}, {d}" for d in deviations
            ]
            continue
        days.append(
            DayPlan(
                day_index=day_sel.day_index,
                phase=phase,
                meals=[
                    MealEntry(
                        slot=m.slot,
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
                    for m in solved
                ],
                totals=day_totals(solved),
            )
        )
    return days, problems


def _llm_for_phase(
    llm: LLMClient | None,
    allowed: list[FoodItem],
    targets: NutritionTargets,
    config: NutritionConfig,
    variant: int,
    phase: PlanPhase,
    catalog: MealCatalog | None = None,
) -> LLMClient:
    """El motor de la fase.

    Con `ANTHROPIC_API_KEY` manda el LLM real. Sin ella, el de PLATOS, que solo
    emite combinaciones que un humano escribió. Si la lista del cliente es tan
    corta que no da para armar ningún plato en algún slot, cae al heurístico
    —que compone rol por rol y siempre produce algo— en vez de fallar.
    """
    from nutriplan.adapters.llm.heuristic import HeuristicSelector
    from nutriplan.adapters.llm.template_selector import InsufficientDishes, TemplateSelector

    if llm is not None and not isinstance(llm, HeuristicSelector | TemplateSelector):
        return llm

    seed = variant + (100 if phase is PlanPhase.NEXT_15 else 0)
    if catalog is not None:
        try:
            return TemplateSelector(
                allowed, catalog, targets.daily, seed=seed, config=config
            )
        except InsufficientDishes as exc:
            logger.warning("template_pool_insufficient", reason=str(exc))
    return HeuristicSelector(allowed, targets.daily.protein_g, seed=seed, config=config)


async def _generate_phase_week(
    *,
    client: Client,
    targets: NutritionTargets,
    allowed: list[FoodItem],
    config: NutritionConfig,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
    variant: int,
    phase: PlanPhase,
    duration_days: int,
    feedback: str | None,
    catalog: MealCatalog | None = None,
) -> tuple[list[DayPlan], list[str]]:
    prompt = load_prompt(prompts_dir, "plan_generation")
    schema = build_selection_schema(allowed)
    foods_by_id = {str(f.id): f for f in allowed}
    selector = _llm_for_phase(llm, allowed, targets, config, variant, phase, catalog)

    user_prompt = build_selection_prompt(
        targets, allowed, config, feedback, duration_days=duration_days, phase=phase
    )
    raw = await selector.select_plan(
        system=prompt.text, prompt=user_prompt, schema=schema, model=model
    )
    selection = PlanSelection.model_validate(raw.model_dump())

    problems = [
        f"fase {phase.value}, día {v.day_index}, {v.slot.value}: {v.reason}"
        for v in validate_selection_structure(selection, foods_by_id)
    ]
    problems += [
        f"fase {phase.value}, variedad: {v.food_name} usado {v.times_used} veces "
        f"(máx. {v.limit})"
        for v in check_variety(
            selection,
            foods_by_id,
            max_protein_repeats=config.generation.max_protein_repeats_per_week,
            max_carb_repeats=config.generation.max_carb_repeats_per_week,
            available=slot_availability(allowed),
        )
    ]
    days, solve_problems = _selection_to_days(
        selection,
        phase=phase,
        foods_by_id=foods_by_id,
        targets=targets,
        config=config,
    )
    problems += solve_problems
    if len(days) != 7:
        problems.append(f"fase {phase.value}: faltan días resueltos ({len(days)}/7)")
    return days, problems


async def generate_cycle(
    *,
    client: Client,
    targets: NutritionTargets,
    allowed: list[FoodItem],
    config: NutritionConfig,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
    variant: int = 0,
    duration_days: int = 15,
    catalog: MealCatalog | None = None,
) -> PlanCycle:
    if duration_days not in (15, 30):
        raise GenerationError("duration_days debe ser 15 o 30")

    prompt = load_prompt(prompts_dir, "plan_generation")
    input_hash = compute_input_hash(
        client,
        targets,
        config.version,
        prompt.version,
        allowed,
        variant,
        duration_days=duration_days,
    )

    feedback: str | None = None
    failures: list[str] = []
    phases = [PlanPhase.FIRST_15]
    if duration_days >= 30:
        phases.append(PlanPhase.NEXT_15)

    for attempt in range(1 + config.generation.max_retries):
        all_days: list[DayPlan] = []
        problems: list[str] = []
        for phase in phases:
            days, phase_problems = await _generate_phase_week(
                client=client,
                targets=targets,
                allowed=allowed,
                config=config,
                llm=llm,
                prompts_dir=prompts_dir,
                model=model,
                variant=variant,
                phase=phase,
                duration_days=duration_days,
                feedback=feedback,
                catalog=catalog,
            )
            problems += phase_problems
            all_days += days

        expected = 7 * len(phases)
        if not problems and len(all_days) == expected:
            logger.info(
                "plan_generated",
                attempts=attempt + 1,
                input_hash=input_hash[:12],
                duration_days=duration_days,
                phases=len(phases),
            )
            return PlanCycle(
                id=uuid4(),
                tenant_id=client.tenant_id,
                client_id=client.id,
                targets_id=targets.id,
                days=all_days,
                duration_days=duration_days,
                variant=variant,
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
        f"No se logró cuadrar el plan tras "
        f"{1 + config.generation.max_retries} intentos. Detalle: "
        + "; ".join(failures[:10])
    )


async def generate_plan_for_client(
    *,
    client: Client,
    targets: NutritionTargets,
    food_repo: FoodRepository,
    plan_repo: PlanRepository,
    client_repo: ClientRepository,
    config: NutritionConfig,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
    variant: int = 0,
    duration_days: int = 15,
    catalog: MealCatalog | None = None,
) -> PlanCycle:
    """Orquesta el plan: 15 días (1 semana) o 30 días (2 semanas en 2 fases)."""
    liked = await food_repo.get_by_ids(client.liked_food_ids)
    banned = set(await client_repo.list_banned_food_ids(client.id))
    allowed = allowed_foods(liked, client.restrictions, banned)
    if not allowed:
        raise GenerationError(
            "El conjunto permitido quedó vacío: revisa alimentos que le gustan, "
            "restricciones y vetos del cliente."
        )

    prompt = load_prompt(prompts_dir, "plan_generation")
    input_hash = compute_input_hash(
        client,
        targets,
        config.version,
        prompt.version,
        allowed,
        variant,
        duration_days=duration_days,
        catalog_version=catalog.version if catalog else "",
    )
    existing = await plan_repo.find_by_input_hash(input_hash)
    if existing:
        logger.info("plan_reused_by_hash", input_hash=input_hash[:12])
        return existing[0]

    cycle = await generate_cycle(
        client=client,
        targets=targets,
        allowed=allowed,
        config=config,
        llm=llm,
        prompts_dir=prompts_dir,
        model=model,
        variant=variant,
        duration_days=duration_days,
        catalog=catalog,
    )
    await plan_repo.add(cycle)
    return cycle
