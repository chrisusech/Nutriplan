"""Carga y escribe el catálogo de restaurantes en YAML.

El archivo es la fuente humana de verdad: va en git y se revisa. La consola
añade marcas y platos aquí, igual que las recetas curadas; no hay tabla SQL.
"""

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from nutriplan.domain.errors import NutriPlanError
from nutriplan.domain.restaurant import Restaurant, RestaurantCatalog, RestaurantDish

_HEADER = (
    "# Macros de restaurantes. Los gramos P/C/G son la fuente de verdad;\n"
    "# las kcal las calcula el código (4P+4C+9G).\n"
)


class RestaurantCatalogError(NutriPlanError):
    """YAML de restaurantes ilegible o inválido."""


def load_restaurant_catalog(path: Path) -> RestaurantCatalog:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return RestaurantCatalog.model_validate(raw)
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise RestaurantCatalogError(f"No se pudo leer el catálogo de restaurantes: {exc}") from exc


def _write(path: Path, catalog: RestaurantCatalog) -> None:
    body = {
        "version": catalog.version,
        "restaurants": [r.model_dump(exclude_none=True) for r in catalog.restaurants],
    }
    try:
        path.write_text(
            _HEADER + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=88),
            encoding="utf-8",
        )
    except OSError as exc:
        raise RestaurantCatalogError(f"No se pudo escribir el catálogo: {exc}") from exc
    cached_restaurant_catalog.cache_clear()


def append_restaurant(path: Path, restaurant: Restaurant) -> bool:
    """Añade una marca. False si el id ya existía."""
    catalog = load_restaurant_catalog(path)
    if any(r.id == restaurant.id for r in catalog.restaurants):
        return False
    catalog.restaurants.append(restaurant)
    _write(path, catalog)
    return True


def append_dish(path: Path, restaurant_id: str, dish: RestaurantDish) -> bool:
    """Añade un plato a una marca. False si el id ya existía en esa marca.

    Si la marca no está, no se inventa: quien llama muestra el error.
    """
    catalog = load_restaurant_catalog(path)
    resto = catalog.restaurant(restaurant_id)
    if resto is None:
        raise RestaurantCatalogError(f"No está el restaurante «{restaurant_id}».")
    if any(d.id == dish.id for d in resto.dishes):
        return False
    resto.dishes.append(dish)
    _write(path, catalog)
    return True


@lru_cache
def cached_restaurant_catalog(path: str) -> RestaurantCatalog:
    return load_restaurant_catalog(Path(path))
