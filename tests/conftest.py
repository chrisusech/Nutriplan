from pathlib import Path

import pytest

from nutriplan.adapters.config_yaml import YamlConfigProvider
from nutriplan.domain.nutrition_config import NutritionConfig

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def nutrition_config() -> NutritionConfig:
    provider = YamlConfigProvider(PROJECT_ROOT / "config" / "nutrition.default.yaml")
    return provider.get_nutrition_config()
