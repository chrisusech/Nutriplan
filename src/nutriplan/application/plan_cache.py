"""La clave de caché de un plan: mismo insumo, mismo menú.

Vive aparte porque la calculan dos sitios que TIENEN que coincidir: la ruta que
arranca el job (para no generar dos veces lo mismo) y la generación (que busca
en caché antes de gastar una llamada, y guarda con esa misma clave).
"""

import hashlib
import json
from datetime import date
from uuid import UUID

from nutriplan.application.refine_plan import REVIEW_PROMPT_VERSION
from nutriplan.domain.meal_template import MealCatalog
from nutriplan.domain.models import Client, FoodItem, NutritionTargets
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.taste import TasteProfile
from nutriplan.ports.llm_client import LLMClient

# La versión del prompt entra en el input_hash: subirla invalida la caché de
# planes, que es exactamente lo que se quiere cuando cambian las reglas.
PLAN_PROMPT_VERSION = 4
PLAN_PROMPT_ID = f"plan_generation.v{PLAN_PROMPT_VERSION}"


def compute_input_hash(
    client: Client,
    targets: NutritionTargets,
    config_version: str,
    prompt_version: str,
    allowed: list[FoodItem],
    variant: int = 0,
    *,
    catalog_version: str = "",
    week_start: date | None = None,
    taste: TasteProfile | None = None,
    on_hand: frozenset[UUID] = frozenset(),
    recent_keys: frozenset[str] = frozenset(),
) -> str:
    """Idempotencia (11.6): mismo insumo → mismo hash → mismo plan.

    `catalog_version` es la versión del catálogo de PLATOS. Sin ella, editar
    meal_templates.yaml no cambia el hash, `find_by_input_hash` devuelve el plan
    viejo y los platos nuevos no aparecen jamás — parecería que el motor está
    roto cuando lo que está roto es la caché.

    `week_start` entra por lo mismo: una semana nueva es un menú nuevo aunque el
    perfil no haya cambiado ni un gramo. Sin ella, quien se mantiene estable
    recibiría siempre el plan de la caché.

    Y `on_hand` por lo mismo otra vez: marcar la nevera cambia el menú, así que si
    no entra aquí, generar después de marcarla devuelve el plan viejo y parece que
    el botón no hace nada.
    """
    snapshot = {
        "catalog_version": catalog_version,
        "variant": variant,
        "week_start": week_start.isoformat() if week_start else "",
        "on_hand_food_ids": sorted(str(fid) for fid in on_hand),
        "recent_dish_keys": sorted(recent_keys),
        # Sin esto, quien pidió "no más pescado" recibiría de la caché el mismo
        # menú con pescado: el perfil no cambió y el hash sería idéntico.
        "taste": taste.fingerprint() if taste else "",
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


def plan_cache_key(
    *,
    client: Client,
    targets: NutritionTargets,
    config: NutritionConfig,
    allowed: list[FoodItem],
    variant: int,
    catalog: MealCatalog | None,
    week_start: date,
    taste: TasteProfile | None,
    llm: LLMClient | None,
    select_foods: bool,
    refine_names: bool,
    on_hand: frozenset[UUID] = frozenset(),
    recent_keys: frozenset[str] = frozenset(),
) -> str:
    """La clave de caché de un plan, calculada en UN solo sitio.

    Quien guarda el plan y quien lo busca tienen que llegar al mismo número. Ya
    pasó dos veces que no: primero por olvidar `catalog_version`, después porque
    solo una de las dos ramas añadía el crítico a la versión del prompt. El
    síntoma es el mismo y es feo — la caché no acierta nunca y regenerar choca
    contra la unicidad de (tenant, input_hash, variant).
    """
    # Cada etapa que la IA ejecuta es parte del insumo: el mismo perfil elegido
    # por el motor y elegido por la IA no dan el mismo menú, y con el crítico
    # apagado no se debe servir de la caché uno que sí lo pasó.
    stages = [PLAN_PROMPT_ID]
    if llm is not None:
        if select_foods:
            stages.append("select")
        if refine_names:
            stages.append(f"review.v{REVIEW_PROMPT_VERSION}")
    return compute_input_hash(
        client,
        targets,
        config.version,
        "+".join(stages),
        allowed,
        variant,
        catalog_version=catalog.version if catalog else "",
        week_start=week_start,
        taste=taste,
        on_hand=on_hand,
        recent_keys=recent_keys,
    )
