"""El motor de la casa: platos del catálogo y, si no alcanzan, heurístico.

Implementa `OfflineEngineFactory`. La preferencia por el catálogo YAML es una
decisión de producto (los platos tienen nombre y receta; el heurístico solo
junta alimentos que cuadran), y el heurístico es la red de seguridad para
cuando el catálogo de esa persona se queda corto.
"""

from uuid import UUID

import structlog

from nutriplan.adapters.llm.heuristic import HeuristicSelector
from nutriplan.adapters.llm.template_selector import InsufficientDishes, TemplateSelector
from nutriplan.domain.meal_template import MealCatalog
from nutriplan.domain.models import FoodItem, MacroTargets
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.taste import TasteProfile
from nutriplan.ports.llm_client import LLMClient

logger = structlog.get_logger(__name__)


def build_offline_engine(
    *,
    allowed: list[FoodItem],
    daily: MacroTargets,
    config: NutritionConfig,
    seed: int,
    catalog: MealCatalog | None = None,
    on_hand_ids: frozenset[UUID] = frozenset(),
    taste: TasteProfile | None = None,
    recent_keys: frozenset[str] = frozenset(),
    recent_templates: frozenset[str] = frozenset(),
) -> LLMClient:
    if catalog is not None:
        try:
            return TemplateSelector(
                allowed,
                catalog,
                daily,
                seed=seed,
                config=config,
                on_hand_ids=on_hand_ids,
                taste=taste,
                recent_keys=recent_keys,
                recent_templates=recent_templates,
            )
        except InsufficientDishes as exc:
            logger.warning("template_pool_insufficient", reason=str(exc))
    # El heurístico no mira la despensa: es la red de seguridad de quien no tiene
    # ni un plato cocinable, y ahí el problema no es aprovechar la nevera.
    return HeuristicSelector(
        allowed,
        daily.protein_g,
        seed=seed,
        config=config,
        daily_carb_g=daily.carb_g,
    )
