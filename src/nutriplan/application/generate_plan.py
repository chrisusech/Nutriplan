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
from nutriplan.application.food_pool import resolve_allowed_foods, shortlist_for_llm
from nutriplan.application.refine_plan import REVIEW_PROMPT_VERSION, refine_week
from nutriplan.domain.dish_recipe import dish_key
from nutriplan.domain.errors import GenerationError, LLMError
from nutriplan.domain.generation_rules import (
    SLOT_STRUCTURE,
    check_variety,
    drop_free_meal,
    slot_availability,
    validate_selection_structure,
)
from nutriplan.domain.macro_split import daily_minus_free_meal, macro_shares
from nutriplan.domain.meal_template import Dish, MealCatalog
from nutriplan.domain.models import (
    DAYS_PER_WEEK,
    Client,
    DayPlan,
    FoodItem,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
    NutritionTargets,
    PlanCycle,
    PlanSelection,
    PlanStatus,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import solve_day_portions, usable_in_slot
from nutriplan.domain.selection_schema import build_selection_schema
from nutriplan.domain.validation import day_totals, validate_day
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.repository import ClientRepository, PlanRepository

logger = structlog.get_logger(__name__)

# La versión del prompt entra en el input_hash: subirla invalida la caché de
# planes, que es exactamente lo que se quiere cuando cambian las reglas.
PLAN_PROMPT_VERSION = 2

_SLOT_ORDER = list(MealSlot)


def _slots_of(config: NutritionConfig) -> list[MealSlot]:
    """Las comidas de este cliente: las que su reparto declara, en orden del día.

    El reparto ya viene recortado a lo que el cliente come (`config.for_slots`),
    así que el motor, el schema y el validador leen la misma fuente.
    """
    return [s for s in _SLOT_ORDER if s in config.meal_distribution]

def compute_input_hash(
    client: Client,
    targets: NutritionTargets,
    config_version: str,
    prompt_version: str,
    allowed: list[FoodItem],
    variant: int = 0,
    *,
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
        "client": {
            # Sin esto, cambiar a un cliente de 4 comidas le devolvería el plan de
            # 5 servido de la caché.
            "meal_slots": [s.value for s in client.meal_slots],
            # Y sin esto, poner (o mover) la comida libre devolvería el plan viejo
            # de la caché: parecería que el botón no hace nada.
            "free_meal": (
                [client.free_meal_day, client.free_meal_slot.value]
                if client.free_meal_slot is not None
                else None
            ),
            "sex": client.sex.value,
            "age": client.age_years,
            "height_cm": client.height_cm,
            "weight_kg": client.weight_kg,
            "goal": client.goal.value,
            "activity": client.activity_level.value,
            "restrictions": sorted(client.restrictions),
            "eating_pattern_raw": client.eating_pattern_raw,
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
    habits: str | None = None,
) -> str:
    slots = _slots_of(config)
    structure_lines = [
        f"- {slot.value}: {SLOT_STRUCTURE[slot].description} "
        f"(máx. {SLOT_STRUCTURE[slot].max_items} alimentos)"
        for slot in slots
    ]
    catalog_lines = [
        f"- {f.id} | {f.name_es} | {f.category.value}"
        for f in sorted(allowed, key=lambda f: (f.category.value, f.name_es))
    ]
    gen = config.generation
    parts = [
        "Menú nutricional de una semana: 7 días.",
        "",
        f"CADA DÍA TIENE {len(slots)} COMIDAS, exactamente estas: "
        f"{', '.join(s.value for s in slots)}.",
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
    if habits and habits.strip():
        parts += [
            "",
            "HÁBITOS DEL CLIENTE (respeta el estilo; no inventes alimentos fuera del catálogo):",
            habits.strip(),
        ]
    if feedback:
        parts += ["", "CORRECCIÓN REQUERIDA (la selección anterior falló):", feedback]
    return "\n".join(parts)


def _free_meal_entry(slot: MealSlot) -> MealEntry:
    """La celda libre: sin alimentos, sin gramos y sin macros que contar."""
    return MealEntry(
        slot=slot,
        items=[],
        computed=MacroTargets(kcal=0.0, protein_g=0.0, carb_g=0.0, fat_g=0.0),
        is_free_meal=True,
    )


def _selection_to_days(
    selection: PlanSelection,
    *,
    foods_by_id: dict[str, FoodItem],
    targets: NutritionTargets,
    config: NutritionConfig,
    free_meal: tuple[int, MealSlot] | None = None,
    dishes: dict[tuple[int, MealSlot], Dish] | None = None,
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
        # El día de la comida libre se porciona y se juzga contra un objetivo MENOR:
        # el suyo menos lo que pesaba esa comida. Así las que quedan conservan su
        # objetivo de siempre y el día suma por debajo — que es lo que significa
        # comerse una pizza. Sin esto, `macro_shares` renormalizaría y las cuatro
        # comidas restantes cargarían con el día entero.
        free_slot = free_meal[1] if free_meal and free_meal[0] == day_sel.day_index else None
        day_daily = daily_minus_free_meal(targets.daily, config, free_slot)
        try:
            solved = solve_day_portions(meals_input, day_daily, config)
        except GenerationError as exc:
            problems.append(f"día {day_sel.day_index}: {exc}")
            continue
        deviations = validate_day(
            solved,
            day_daily,
            config,
            shares=macro_shares(meals_input, config),
        )
        if deviations:
            problems += [
                f"día {day_sel.day_index}, {d}" for d in deviations
            ]
            continue
        meals = []
        for m in solved:
            dish = (dishes or {}).get((day_sel.day_index, m.slot))
            food_ids = [p.food_id for p in m.portions]
            meals.append(
                MealEntry(
                    slot=m.slot,
                    template_id=dish.template_id if dish else None,
                    dish_name=dish.name if dish else None,
                    dish_key=dish_key(dish.template_id, food_ids) if dish else None,
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
            )
        if free_slot is not None:
            meals.append(_free_meal_entry(free_slot))
            meals.sort(key=lambda m: _SLOT_ORDER.index(m.slot))  # el orden del día
        days.append(
            DayPlan(
                day_index=day_sel.day_index,
                meals=meals,
                # La comida libre no suma: los totales del día son los de lo que se
                # pesa, y por eso el día sale por debajo del objetivo. Es la verdad.
                totals=day_totals(solved),
            )
        )
    return days, problems


def _selector_for(
    llm: LLMClient | None,
    allowed: list[FoodItem],
    targets: NutritionTargets,
    config: NutritionConfig,
    variant: int,
    catalog: MealCatalog | None = None,
    *,
    select_foods: bool = False,
) -> LLMClient:
    """El motor de la fase.

    Por defecto (beta) el LLM no elige alimentos: TemplateSelector arma platos
    escritos por humanos. `select_foods=True` restaura el camino legacy en el
    que el modelo propone food_ids. Si el catálogo no alcanza, HeuristicSelector.
    """
    from nutriplan.adapters.llm.heuristic import HeuristicSelector
    from nutriplan.adapters.llm.template_selector import InsufficientDishes, TemplateSelector

    if (
        select_foods
        and llm is not None
        and not isinstance(llm, HeuristicSelector | TemplateSelector)
    ):
        return llm

    seed = variant
    if catalog is not None:
        try:
            return TemplateSelector(
                allowed, catalog, targets.daily, seed=seed, config=config
            )
        except InsufficientDishes as exc:
            logger.warning("template_pool_insufficient", reason=str(exc))
    return HeuristicSelector(allowed, targets.daily.protein_g, seed=seed, config=config)


async def _generate_week(
    *,
    client: Client,
    targets: NutritionTargets,
    allowed: list[FoodItem],
    config: NutritionConfig,
    llm: LLMClient | None,
    prompts_dir: Path,
    model: str,
    variant: int,
    feedback: str | None,
    catalog: MealCatalog | None = None,
    select_foods: bool = False,
) -> tuple[list[DayPlan], list[str]]:
    from nutriplan.adapters.llm.heuristic import HeuristicSelector
    from nutriplan.adapters.llm.template_selector import TemplateSelector

    prompt = load_prompt(prompts_dir, "plan_generation", PLAN_PROMPT_VERSION)
    slots = _slots_of(config)
    selector = _selector_for(
        llm, allowed, targets, config, variant, catalog, select_foods=select_foods
    )
    offline = isinstance(selector, HeuristicSelector | TemplateSelector)

    # Al modelo se le enseña un catálogo corto: los 172 alimentos gastan ~6.400
    # tokens de entrada y el nivel gratis da 8.000 por minuto. Los selectores
    # offline no pagan tokens, así que ven el pool entero.
    visible = allowed if offline else shortlist_for_llm(allowed)
    schema = build_selection_schema(visible, slots)
    foods_by_id = {str(f.id): f for f in allowed}

    habits = None if offline else client.eating_pattern_raw
    user_prompt = build_selection_prompt(
        targets,
        visible,
        config,
        feedback,
        habits=habits,
    )
    try:
        raw = await selector.select_plan(
            system=prompt.text, prompt=user_prompt, schema=schema, model=model
        )
    except LLMError as exc:
        # Quedarse sin cuota, o que el modelo no quepa en ella, no puede dejar a
        # nadie sin menú: el motor de platos determinista lo arma igual. Se pierde
        # el toque del modelo en la SELECCIÓN, no el menú.
        if offline:
            raise
        logger.warning("selection_fell_back_to_engine", error=str(exc))
        selector = _selector_for(None, allowed, targets, config, variant, catalog)
        schema = build_selection_schema(allowed, slots)
        raw = await selector.select_plan(
            system=prompt.text,
            prompt=build_selection_prompt(targets, allowed, config, feedback),
            schema=schema,
            model="engine-v1",
        )
    selection = PlanSelection.model_validate(raw.model_dump())
    # El selector emite las cinco comidas del día: no sabe de comidas libres, ni
    # tiene por qué. La celda se quita aquí, en la frontera, y de aquí para adentro
    # todo el pipeline trabaja con un día de cuatro comidas sin enterarse de nada.
    free_meal = client.free_meal
    selection = drop_free_meal(selection, free_meal)

    problems = [
        f"día {v.day_index}, {v.slot.value}: {v.reason}"
        for v in validate_selection_structure(
            selection, foods_by_id, slots, free_meal=free_meal
        )
    ]
    problems += [
        f"variedad: {v.food_name} usado {v.times_used} veces "
        f"(máx. {v.limit})"
        for v in check_variety(
            selection,
            foods_by_id,
            max_protein_repeats=config.generation.max_protein_repeats_per_week,
            max_carb_repeats=config.generation.max_carb_repeats_per_week,
            # Las opciones REALES del cliente en cada slot, no las nominales: una
            # lata de atún declara "almuerzo" pero no cuadra un almuerzo de 36 g de
            # proteína, y el motor no la va a usar ahí.
            available=slot_availability(allowed, usable_in_slot(targets.daily, config)),
        )
    ]
    days, solve_problems = _selection_to_days(
        selection,
        foods_by_id=foods_by_id,
        targets=targets,
        config=config,
        free_meal=free_meal,
        dishes=getattr(selector, "last_dishes", None),
    )
    problems += solve_problems
    if len(days) != 7:
        problems.append(f"faltan días resueltos ({len(days)}/7)")
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
    catalog: MealCatalog | None = None,
    select_foods: bool = False,
    refine_names: bool = False,
) -> PlanCycle:
    prompt = load_prompt(prompts_dir, "plan_generation", PLAN_PROMPT_VERSION)
    # El hash con el que se GUARDA tiene que ser el mismo con el que se BUSCA
    # (`generate_plan_for_client`). Sin `catalog_version` aquí, el plan se guardaba
    # con un hash que nadie consulta: la caché no acertaba nunca y, al regenerar el
    # mismo plan, la inserción chocaba contra la unicidad de (tenant, hash, variant).
    uses_llm_in_plan = select_foods or refine_names
    input_hash = compute_input_hash(
        client,
        targets,
        config.version,
        (
            f"{prompt.version}+review.v{REVIEW_PROMPT_VERSION}"
            if uses_llm_in_plan and llm
            else prompt.version
        ),
        allowed,
        variant,
        catalog_version=catalog.version if catalog else "",
    )

    feedback: str | None = None
    failures: list[str] = []
    plan_model = model if select_foods else "engine-v1"

    for attempt in range(1 + config.generation.max_retries):
        days, problems = await _generate_week(
            client=client,
            targets=targets,
            allowed=allowed,
            config=config,
            llm=llm,
            prompts_dir=prompts_dir,
            model=model,
            variant=variant,
            feedback=feedback,
            catalog=catalog,
            select_foods=select_foods,
        )

        if not problems and len(days) == DAYS_PER_WEEK:
            # La pasada de sabor. En beta viene apagada (`refine_names=False`).
            refined = None
            if refine_names:
                refined = await refine_week(
                    days=days, client=client, allowed=allowed, config=config,
                    daily=targets.daily, llm=llm, prompts_dir=prompts_dir, model=model,
                )
            if refined is not None:
                days = refined.days
            logger.info(
                "plan_generated",
                attempts=attempt + 1,
                input_hash=input_hash[:12],
                refined=refined is not None,
            )
            return PlanCycle(
                id=uuid4(),
                tenant_id=client.tenant_id,
                client_id=client.id,
                targets_id=targets.id,
                days=days,
                variant=variant,
                status=PlanStatus.DRAFT,
                config_version=config.version,
                prompt_version=prompt.version,
                model=plan_model,
                input_hash=input_hash,
                created_at=datetime.now(UTC),
                refined_at=datetime.now(UTC) if refined is not None else None,
                refine_model=model if refined is not None else None,
                refine_prompt_version=(
                    f"plan_review.v{REVIEW_PROMPT_VERSION}" if refined is not None else None
                ),
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
    catalog: MealCatalog | None = None,
    select_foods: bool = False,
    refine_names: bool = False,
) -> PlanCycle:
    """Orquesta el menú de la semana, de punta a punta."""
    allowed = await resolve_allowed_foods(
        client, food_repo=food_repo, client_repo=client_repo
    )
    if not allowed:
        raise GenerationError(
            "No quedó ningún alimento disponible: revisa tus restricciones y "
            "lo que marcaste que no quieres ver."
        )

    prompt = load_prompt(prompts_dir, "plan_generation", PLAN_PROMPT_VERSION)
    input_hash = compute_input_hash(
        client,
        targets,
        config.version,
        prompt.version,
        allowed,
        variant,
        catalog_version=catalog.version if catalog else "",
    )
    existing = await plan_repo.find_by_input_hash(input_hash)
    if existing:
        # El mismo insumo devuelve el mismo plan. Se activa igualmente (el entrenador
        # pidió ESTE plan), pero no sube de versión: no hay plan nuevo que numerar.
        logger.info("plan_reused_by_hash", input_hash=input_hash[:12])
        await client_repo.set_active_plan(client.id, existing[0].id)
        return existing[0]

    # Número provisional del borrador: la siguiente versión DEFINITIVA. Los
    # borradores no consumen número; se confirma al aprobar. `variant` es su gemelo
    # técnico y entra en el input_hash, así que dos borradores nunca colisionan en
    # la caché aunque los macros no hayan cambiado.
    version = 1 + await plan_repo.count_approved_for_client(client.id)

    cycle = await generate_cycle(
        client=client,
        targets=targets,
        allowed=allowed,
        config=config,
        llm=llm,
        prompts_dir=prompts_dir,
        model=model,
        variant=variant,
        catalog=catalog,
        select_foods=select_foods,
        refine_names=refine_names,
    )
    cycle = cycle.model_copy(update={"version": version})
    # Solo lo definitivo se guarda: hay un único borrador vivo por cliente, y este
    # nuevo lo reemplaza. Regenerar no acumula borradores.
    await plan_repo.delete_draft_for_client(client.id)
    await plan_repo.add(cycle)
    # El plan recién hecho es EL plan; el anterior queda archivado y consultable.
    await client_repo.set_active_plan(client.id, cycle.id)
    return cycle
