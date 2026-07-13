"""Carga el catálogo de platos desde YAML.

Los errores del catálogo se lanzan AL CARGAR (en el arranque de la app), no en
mitad de una generación: un plato mal declarado tiene que ser un fallo ruidoso
del despliegue, no un plan raro para un cliente.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from nutriplan.domain.meal_template import (
    Component,
    FoodClass,
    MealCatalog,
    MealCatalogError,
    MealTemplate,
)
from nutriplan.domain.models import FoodCategory, MealSlot, UnitGranularity


def _food_class(name: str, raw: dict[str, Any]) -> FoodClass:
    ratios = {
        attr: (spec["of"], float(spec["value"]))
        for attr, spec in (raw.get("max_ratio") or {}).items()
    }
    granularity = raw.get("unit_granularity")
    return FoodClass(
        name=name,
        label=raw.get("label", name),
        categories=frozenset(FoodCategory(c) for c in raw.get("categories", ())),
        all_tags=frozenset(raw.get("all_tags", ())),
        any_tags=frozenset(raw.get("any_tags", ())),
        none_tags=frozenset(raw.get("none_tags", ())),
        slots_any=frozenset(MealSlot(s) for s in raw.get("slots_any", ())),
        names_any=frozenset(raw.get("names_any", ())),
        min_per_100g={k: float(v) for k, v in (raw.get("min_per_100g") or {}).items()},
        max_per_100g={k: float(v) for k, v in (raw.get("max_per_100g") or {}).items()},
        max_ratio=ratios,
        unit_granularity=UnitGranularity(granularity) if granularity else None,
        max_unit_g=float(raw["max_unit_g"]) if raw.get("max_unit_g") else None,
    )


def _template(raw: dict[str, Any]) -> MealTemplate:
    return MealTemplate(
        id=raw["id"],
        name=raw["name"],
        slots=tuple(MealSlot(s) for s in raw["slots"]),
        components=tuple(
            Component(
                role=FoodCategory(c["role"]),
                selector=c["sel"],
                optional=bool(c.get("optional", False)),
            )
            for c in raw["components"]
        ),
        free_salad=bool(raw.get("free_salad", False)),
    )


def load_meal_catalog(classes_path: Path, templates_path: Path) -> MealCatalog:
    try:
        classes_raw = yaml.safe_load(classes_path.read_text(encoding="utf-8")) or {}
        templates_raw = yaml.safe_load(templates_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise MealCatalogError(f"No se pudo leer el catálogo de platos: {exc}") from exc

    classes = {
        name: _food_class(name, raw or {})
        for name, raw in (classes_raw.get("classes") or {}).items()
    }
    templates = tuple(_template(raw) for raw in (templates_raw.get("templates") or ()))
    # La versión combina las dos: tocar cualquiera de los dos ficheros invalida la
    # caché de planes por input_hash.
    version = f"{classes_raw.get('version', '0')}+{templates_raw.get('version', '0')}"
    return MealCatalog(version=version, classes=classes, templates=templates)


@lru_cache(maxsize=4)
def cached_meal_catalog(classes_path: Path, templates_path: Path) -> MealCatalog:
    return load_meal_catalog(classes_path, templates_path)
