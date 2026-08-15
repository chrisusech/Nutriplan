"""Puerto del motor determinista que arma la semana sin IA."""

from typing import Protocol
from uuid import UUID

from nutriplan.domain.meal_template import MealCatalog
from nutriplan.domain.models import FoodItem, MacroTargets
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.taste import TasteProfile
from nutriplan.ports.llm_client import LLMClient


class OfflineEngineFactory(Protocol):
    """Devuelve el motor que elige los platos de una semana sin proveedor.

    Existe para que la generación no tenga que conocer a los selectores
    concretos: quién arma la semana cuando no hay IA (o cuando la IA falla) es
    una decisión de cableado, y el cableado vive en el composition root.

    `seed` es lo que hace que «generar otra semana» dé otra semana.

    `on_hand_ids` es lo que la persona dice tener ya en casa esta semana: el motor
    lo prefiere al armar los días, sin que eso mande sobre la variedad.

    `taste` es el veto y el cariño de las notas: sin él, calificar no cambia
    el menú de la semana siguiente.
    """

    def __call__(
        self,
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
    ) -> LLMClient: ...
