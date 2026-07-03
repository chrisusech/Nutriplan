"""Puerto de acceso a la estrategia nutricional versionada."""

from typing import Protocol

from nutriplan.domain.nutrition_config import NutritionConfig


class ConfigProvider(Protocol):
    def get_nutrition_config(self) -> NutritionConfig: ...
