"""Adaptador YAML del puerto ConfigProvider.

Soporta overrides por tenant (nutrition.<tenant>.yaml fusionado sobre el
default) según la sección 16 de la spec; en Nivel 1 solo se usa el default.
"""

from pathlib import Path

import yaml

from nutriplan.domain.nutrition_config import NutritionConfig


class YamlConfigProvider:
    def __init__(self, default_path: Path, override_path: Path | None = None) -> None:
        self._default_path = default_path
        self._override_path = override_path
        self._cached: NutritionConfig | None = None

    def get_nutrition_config(self) -> NutritionConfig:
        if self._cached is None:
            data = yaml.safe_load(self._default_path.read_text(encoding="utf-8"))
            if self._override_path is not None and self._override_path.exists():
                override = yaml.safe_load(self._override_path.read_text(encoding="utf-8")) or {}
                data = _deep_merge(data, override)
            self._cached = NutritionConfig.model_validate(data)
        return self._cached


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged
