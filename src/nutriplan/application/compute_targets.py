"""Caso de uso ComputeTargets (Módulo 2): Client → NutritionTargets persistidos.

Función delgada: el cálculo vive en domain/calculation.py; aquí solo se
orquesta config + persistencia. Los overrides del entrenador (9.4) se pasan
tal cual y quedan registrados como procedencia.
"""

import structlog

from nutriplan.domain.calculation import compute_targets
from nutriplan.domain.models import Client, MacroFormula, NutritionTargets
from nutriplan.ports.config_provider import ConfigProvider
from nutriplan.ports.repository import TargetsRepository

logger = structlog.get_logger(__name__)


async def compute_and_store_targets(
    *,
    client: Client,
    config_provider: ConfigProvider,
    targets_repo: TargetsRepository,
    formula: MacroFormula | None = None,
    overrides: dict[str, float] | None = None,
) -> NutritionTargets:
    # El reparto por comida se ajusta a las comidas que hace ESTE cliente: quien
    # come cuatro no reparte su día en cinco.
    config = config_provider.get_nutrition_config().for_slots(client.meal_slots)
    targets = compute_targets(client, config, formula=formula, overrides=overrides)
    await targets_repo.add(targets)
    logger.info(
        "targets_computed",
        client_id=str(client.id),
        kcal=targets.daily.kcal,
        formula=targets.formula.model_dump(exclude_none=True),
        overrides=bool(overrides),
    )
    return targets
