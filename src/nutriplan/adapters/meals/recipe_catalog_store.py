"""Carga y escribe el catálogo curado de recetas en YAML.

El archivo es la fuente humana de verdad: va en git, se revisa y sobrevive a un
borrado de la base. Por eso la consola no edita la caché para "curar" una
receta, sino que la escribe aquí.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from nutriplan.domain.errors import NutriPlanError
from nutriplan.domain.recipe_catalog import CuratedRecipe, RecipeCatalog

_HEADER = "# Catálogo curado. Los gramos los pone el plan; aquí solo nombre y pasos.\n"


class RecipeCatalogError(NutriPlanError):
    """YAML de recetas curadas ilegible o inválido."""


def load_recipe_catalog(path: Path) -> RecipeCatalog:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return RecipeCatalog.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise RecipeCatalogError(f"No se pudo leer el catálogo de recetas: {exc}") from exc


def append_recipe(path: Path, entry: CuratedRecipe) -> bool:
    """Añade una receta al YAML. False si ya estaba (mismo id o mismos alimentos).

    Se reescribe el archivo entero desde el modelo ya validado: así un catálogo
    que no se pueda releer nunca llega a escribirse.
    """
    catalog = load_recipe_catalog(path)
    tokens = sorted(entry.foods)
    if any(r.id == entry.id or sorted(r.foods) == tokens for r in catalog.recipes):
        return False

    catalog.recipes.append(entry)
    body = {
        "version": catalog.version,
        "recipes": [r.model_dump(exclude_none=True) for r in catalog.recipes],
    }
    try:
        path.write_text(
            _HEADER + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=88),
            encoding="utf-8",
        )
    except OSError as exc:
        raise RecipeCatalogError(f"No se pudo escribir el catálogo: {exc}") from exc
    cached_recipe_catalog.cache_clear()
    return True


@lru_cache
def cached_recipe_catalog(path: str) -> RecipeCatalog:
    return load_recipe_catalog(Path(path))
