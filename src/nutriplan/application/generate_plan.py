"""Generación del plan: bucle propuesta → cálculo → validación → reintento.

La IA propone QUÉ alimentos (schema con enum de ids permitidos); el código
calcula CUÁNTO (portion solver), valida contra tolerancias y reintenta con
retroalimentación. Tras max_retries sin cuadrar → GenerationError: el caso va
a revisión humana, nunca se entrega un plan fuera de tolerancia sin marca.

Las tres piezas que este bucle usa viven aparte porque se leen y se cambian por
separado: el prompt (`plan_prompt`), el armado del día con sus gramos
(`plan_assembly`) y la clave de caché (`plan_cache`).
"""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import structlog

from nutriplan.application.food_pool import resolve_allowed_foods, shortlist_for_llm
from nutriplan.application.plan_assembly import selection_to_days
from nutriplan.application.plan_cache import PLAN_PROMPT_ID, PLAN_PROMPT_VERSION, plan_cache_key
from nutriplan.application.plan_prompt import build_selection_prompt, slots_of
from nutriplan.application.prompts import load_prompt
from nutriplan.application.recent_dishes import dishes_of_previous_week
from nutriplan.application.refine_plan import REVIEW_PROMPT_VERSION, refine_week
from nutriplan.domain.critique import food_aliases
from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.errors import GenerationError, LLMError
from nutriplan.domain.generation_rules import (
    check_variety,
    drop_free_meal,
    slot_availability,
    validate_selection_structure,
)
from nutriplan.domain.meal_template import MealCatalog
from nutriplan.domain.models import (
    DAYS_PER_WEEK,
    Client,
    DayPlan,
    FoodItem,
    MealSlot,
    NutritionTargets,
    PlanCycle,
    PlanSelection,
    PlanStatus,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import usable_in_slot
from nutriplan.domain.proven import MIN_RATING, MIN_RATINGS, ProvenDish, proven_dishes
from nutriplan.domain.selection_schema import (
    build_selection_schema,
    resolve_selection_aliases,
)
from nutriplan.domain.taste import TasteProfile
from nutriplan.domain.week import iso_week_start
from nutriplan.ports.food_repository import FoodRepository
from nutriplan.ports.llm_client import LLMClient
from nutriplan.ports.plan_selector import OfflineEngineFactory
from nutriplan.ports.repository import ClientRepository, PlanRepository

logger = structlog.get_logger(__name__)

_SLOT_ORDER = list(MealSlot)


class ProvenLibrary(Protocol):
    """La biblioteca de recetas, vista desde la generación: solo lo bien votado."""

    async def top_rated(
        self, *, min_rating: float, min_count: int, limit: int = 200
    ) -> list[DishRecipe]: ...


async def _proven_for(
    library: ProvenLibrary | None, allowed: list[FoodItem]
) -> list[ProvenDish] | None:
    """Platos probados que caben en el catálogo de esta persona.

    Un fallo aquí no puede costar el menú: la biblioteca es una ayuda, y sin
    ella la generación es exactamente la de antes.
    """
    if library is None:
        return None
    try:
        rated = await library.top_rated(min_rating=MIN_RATING, min_count=MIN_RATINGS)
    except Exception as exc:  # noqa: BLE001 - la sugerencia nunca bloquea el menú
        logger.warning("proven_dishes_skipped", error=str(exc))
        return None
    return proven_dishes(rated, {f.id for f in allowed})


def _on_hand_names(on_hand: frozenset[UUID], visible: list[FoodItem]) -> list[str] | None:
    """Lo de casa, con nombre, y solo lo que la IA tiene delante.

    Al modelo se le enseña un catálogo corto: nombrarle un alimento que no puede
    elegir es invitarle a inventarse un id.
    """
    names = [f.name_es for f in visible if f.id in on_hand]
    return names or None


async def _generate_week(
    *,
    client: Client,
    targets: NutritionTargets,
    allowed: list[FoodItem],
    config: NutritionConfig,
    llm: LLMClient | None,
    offline_engine: OfflineEngineFactory,
    prompts_dir: Path,
    model: str,
    variant: int,
    feedback: str | None,
    catalog: MealCatalog | None = None,
    select_foods: bool = False,
    taste: TasteProfile | None = None,
    proven: list[ProvenDish] | None = None,
    on_hand: frozenset[UUID] = frozenset(),
    recent_keys: frozenset[str] = frozenset(),
    recent_templates: frozenset[str] = frozenset(),
) -> tuple[list[DayPlan], list[str], str]:
    def _engine() -> LLMClient:
        return offline_engine(
            allowed=allowed,
            daily=targets.daily,
            config=config,
            seed=variant,
            catalog=catalog,
            on_hand_ids=on_hand,
            taste=taste,
            recent_keys=recent_keys,
            recent_templates=recent_templates,
        )

    prompt = load_prompt(prompts_dir, "plan_generation", PLAN_PROMPT_VERSION)
    slots = slots_of(config)
    # Con `select_foods` la IA elige los alimentos (y el código los gramos);
    # si no, la semana la arma el motor de la casa.
    if select_foods and llm is not None:
        selector, offline = llm, False
    else:
        selector, offline = _engine(), True
    source_model = "engine-v1" if offline else model

    # Al modelo se le enseña un catálogo corto por categoría (ver LLM_SHORTLIST).
    # Los selectores offline ven el pool entero.
    visible = allowed if offline else shortlist_for_llm(allowed)
    aliases = None if offline else food_aliases(visible)
    schema = build_selection_schema(visible, slots, use_aliases=aliases is not None)
    foods_by_id = {str(f.id): f for f in allowed}

    habits = None if offline else client.eating_pattern_raw
    user_prompt = build_selection_prompt(
        targets,
        visible,
        config,
        feedback,
        habits=habits,
        dislikes=None if offline else (client.dislikes or None),
        context_tags=None if offline else (client.context_tags or None),
        city=None if offline else client.city,
        aliases=aliases,
        taste=taste,
        proven=None if offline else proven,
        on_hand=None if offline else _on_hand_names(on_hand, visible),
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
        selector = _engine()
        # El mismo recorte que la vía normal. Con el catálogo grande, construir
        # el `Literal` sobre el pool entero genera un enum de miles de opciones:
        # justo lo que esta rama tiene que evitar, porque es la que corre cuando
        # el modelo ya falló una vez.
        fallback_visible = shortlist_for_llm(allowed)
        schema = build_selection_schema(fallback_visible, slots, use_aliases=False)
        raw = await selector.select_plan(
            system=prompt.text,
            prompt=build_selection_prompt(targets, fallback_visible, config, feedback),
            schema=schema,
            model="engine-v1",
        )
        source_model = "engine-v1"
    if aliases is not None and source_model != "engine-v1":
        selection = PlanSelection.model_validate(resolve_selection_aliases(raw, aliases))
    else:
        selection = PlanSelection.model_validate(raw.model_dump())
    # La comida libre se eliminó: quien sale a comer elige un plato de
    # restaurante con macros reales. El selector sigue emitiendo todas las
    # comidas del día.
    selection = drop_free_meal(selection, None)

    problems = [
        f"día {v.day_index}, {v.slot.value}: {v.reason}"
        for v in validate_selection_structure(selection, foods_by_id, slots, free_meal=None)
    ]
    problems += [
        f"variedad: {v.food_name} usado {v.times_used} veces (máx. {v.limit})"
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
    days, solve_problems = selection_to_days(
        selection,
        foods_by_id=foods_by_id,
        targets=targets,
        config=config,
        free_meal=None,
        dishes=getattr(selector, "last_dishes", None),
    )
    problems += solve_problems
    if len(days) != 7:
        problems.append(f"faltan días resueltos ({len(days)}/7)")
    return days, problems, source_model


async def generate_cycle(
    *,
    client: Client,
    targets: NutritionTargets,
    allowed: list[FoodItem],
    config: NutritionConfig,
    llm: LLMClient | None,
    offline_engine: OfflineEngineFactory,
    prompts_dir: Path,
    model: str,
    input_hash: str,
    variant: int = 0,
    catalog: MealCatalog | None = None,
    select_foods: bool = False,
    refine_names: bool = False,
    refine_model: str | None = None,
    week_start: date | None = None,
    taste: TasteProfile | None = None,
    proven: list[ProvenDish] | None = None,
    on_hand: frozenset[UUID] = frozenset(),
    recent_keys: frozenset[str] = frozenset(),
    recent_templates: frozenset[str] = frozenset(),
) -> PlanCycle:
    week = week_start or iso_week_start()

    feedback: str | None = None
    failures: list[str] = []
    plan_model = "engine-v1"
    # Con LLM_SELECT_FOODS=false, `model` es "engine-v1" (procedencia del plan).
    # El crítico / recetas necesitan el modelo real de Groq u otro proveedor.
    critic_model = (refine_model or "").strip() or (model if model != "engine-v1" else "")
    # Si el proveedor cae (429/créditos), no quemar más intentos en la misma
    # llamada: el motor determinista ya entró y los reintentos LLM son inútiles.
    active_llm = llm
    active_select = select_foods

    for attempt in range(1 + config.generation.max_retries):
        # Cada intento recrea el selector (calls=0): sin desplazar la semilla
        # el motor offline repetiría la misma semana imposible.
        days, problems, plan_model = await _generate_week(
            client=client,
            targets=targets,
            allowed=allowed,
            config=config,
            llm=active_llm,
            offline_engine=offline_engine,
            prompts_dir=prompts_dir,
            model=model,
            variant=variant + attempt,
            feedback=feedback,
            catalog=catalog,
            select_foods=active_select,
            taste=taste,
            proven=proven,
            on_hand=on_hand,
            recent_keys=recent_keys,
            recent_templates=recent_templates,
        )
        if active_select and active_llm is not None and plan_model == "engine-v1":
            active_llm = None
            active_select = False

        if not problems and len(days) == DAYS_PER_WEEK:
            # Pasada crítica soft (nombres + swaps + verdicto). Apagar con
            # LLM_REFINE_NAMES=false; sin LLM o si falla, el menú se queda.
            refined = None
            if refine_names and critic_model:
                refined = await refine_week(
                    days=days,
                    client=client,
                    allowed=allowed,
                    config=config,
                    daily=targets.daily,
                    # `active_llm`, no `llm`: si el proveedor acaba de tumbar la
                    # selección (429, sin créditos), el crítico iba a golpear al
                    # mismo proveedor muerto en cada reintento.
                    llm=active_llm,
                    prompts_dir=prompts_dir,
                    model=critic_model,
                    taste=taste,
                )
            if refined is not None:
                days = refined.days
            logger.info(
                "plan_generated",
                attempts=attempt + 1,
                input_hash=input_hash[:12],
                refined=refined is not None,
                selection_model=plan_model,
            )
            return PlanCycle(
                id=uuid4(),
                tenant_id=client.tenant_id,
                client_id=client.id,
                targets_id=targets.id,
                days=days,
                week_start=week,
                variant=variant,
                status=PlanStatus.DRAFT,
                config_version=config.version,
                prompt_version=PLAN_PROMPT_ID,
                model=plan_model,
                input_hash=input_hash,
                created_at=datetime.now(UTC),
                refined_at=datetime.now(UTC) if refined is not None else None,
                refine_model=critic_model if refined is not None else None,
                refine_prompt_version=(
                    f"plan_review.v{REVIEW_PROMPT_VERSION}" if refined is not None else None
                ),
            )

        failures = problems
        feedback = "\n".join(f"- {p}" for p in problems[:20])
        logger.warning("plan_retry", attempt=attempt + 1, problems=len(problems))

    # La IA respondió pero eligió combos imposibles de porcionar (carbos bajos
    # en desayuno, proteína en snacks de solo fruta…). El motor determinista
    # conoce densidades y topes del catálogo: mejor un menú genérico que ninguno.
    if select_foods and llm is not None:
        logger.warning(
            "selection_fell_back_to_engine_after_validation",
            failures=len(failures),
        )
        days, problems, plan_model = await _generate_week(
            client=client,
            targets=targets,
            allowed=allowed,
            config=config,
            llm=None,
            offline_engine=offline_engine,
            prompts_dir=prompts_dir,
            model=model,
            variant=variant,
            feedback=None,
            catalog=catalog,
            select_foods=False,
            taste=taste,
            on_hand=on_hand,
        )
        if not problems and len(days) == DAYS_PER_WEEK:
            logger.info(
                "plan_generated",
                attempts="engine-fallback",
                input_hash=input_hash[:12],
                refined=False,
                selection_model=plan_model,
            )
            return PlanCycle(
                id=uuid4(),
                tenant_id=client.tenant_id,
                client_id=client.id,
                targets_id=targets.id,
                days=days,
                week_start=week,
                variant=variant,
                status=PlanStatus.DRAFT,
                config_version=config.version,
                prompt_version=PLAN_PROMPT_ID,
                model=plan_model,
                input_hash=input_hash,
                created_at=datetime.now(UTC),
            )

    raise GenerationError(
        f"No se logró cuadrar el plan tras "
        f"{1 + config.generation.max_retries} intentos. Detalle: " + "; ".join(failures[:10])
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
    offline_engine: OfflineEngineFactory,
    prompts_dir: Path,
    model: str,
    variant: int = 0,
    catalog: MealCatalog | None = None,
    select_foods: bool = False,
    refine_names: bool = False,
    refine_model: str | None = None,
    week_start: date | None = None,
    taste: TasteProfile | None = None,
    library: ProvenLibrary | None = None,
    activate: bool = True,
) -> PlanCycle:
    """Orquesta el menú de la semana, de punta a punta."""
    week = week_start or iso_week_start()
    allowed = await resolve_allowed_foods(client, food_repo=food_repo, client_repo=client_repo)
    if not allowed:
        raise GenerationError(
            "No quedó ningún alimento disponible: revisa tus restricciones y "
            "lo que marcaste que no quieres ver."
        )
    # Si las preferencias no alcanzan un slot (arepa sola vs 128 g de carbo),
    # completamos con el mínimo del universo para que el solver pueda cuadrar.
    from nutriplan.application.food_pool import supplement_pool_for_targets

    universe = await food_repo.list_universe()
    disliked = {d.strip().lower() for d in client.dislikes if d.strip()}
    banned = set(await client_repo.list_banned_food_ids(client.id))
    # Lo que la IA leyó en los comentarios («no más pollo») es un veto duro,
    # igual que un ban: si solo se penaliza en el coste, el motor lo sirve
    # igual cuando el pool es corto.
    if taste is not None:
        banned |= set(taste.avoid_food_ids)
        allowed = [f for f in allowed if f.id not in banned]
    allowed = supplement_pool_for_targets(
        allowed,
        universe,
        daily=targets.daily,
        config=config,
        disliked=disliked,
        banned=banned,
        restrictions=client.restrictions,
    )

    # Lo que ya salió bien, acotado a lo que ESTA persona puede comer. Fuera del
    # input_hash a propósito: la biblioteca cambia con cada nota que alguien
    # pone, y meterla en la clave dejaría la caché sin acertar nunca.
    proven = await _proven_for(library, allowed)

    # Lo que dice tener en casa ESTA semana. Sí entra en el input_hash: marcar la
    # nevera y regenerar tiene que dar otro menú, no el de la caché.
    on_hand = frozenset(await client_repo.list_pantry_food_ids(client.id, week))
    recent_keys, recent_templates = dishes_of_previous_week(
        await plan_repo.list_for_client(client.id), week
    )

    input_hash = plan_cache_key(
        client=client,
        targets=targets,
        config=config,
        allowed=allowed,
        variant=variant,
        catalog=catalog,
        week_start=week,
        taste=taste,
        llm=llm,
        select_foods=select_foods,
        refine_names=refine_names,
        on_hand=on_hand,
        recent_keys=recent_keys,
    )
    existing = await plan_repo.find_by_input_hash(input_hash)
    if existing:
        # El mismo insumo devuelve el mismo plan. Se activa igualmente (el entrenador
        # pidió ESTE plan), pero no sube de versión: no hay plan nuevo que numerar.
        logger.info("plan_reused_by_hash", input_hash=input_hash[:12])
        if activate:
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
        offline_engine=offline_engine,
        prompts_dir=prompts_dir,
        model=model,
        input_hash=input_hash,
        variant=variant,
        catalog=catalog,
        select_foods=select_foods,
        refine_names=refine_names,
        refine_model=refine_model,
        week_start=week,
        taste=taste,
        proven=proven,
        on_hand=on_hand,
        recent_keys=recent_keys,
        recent_templates=recent_templates,
    )
    cycle = cycle.model_copy(update={"version": version})
    # Un único borrador vivo POR SEMANA: regenerar reemplaza el de esta semana y
    # deja intactas las anteriores, que son el historial del seguimiento.
    await plan_repo.delete_draft_for_client(client.id, week_start=week)
    await plan_repo.add(cycle)
    # El plan recién hecho es EL plan, salvo el tick del domingo: ese menú
    # espera al lunes para no tapar el que todavía se come.
    if activate:
        await client_repo.set_active_plan(client.id, cycle.id)
    return cycle
