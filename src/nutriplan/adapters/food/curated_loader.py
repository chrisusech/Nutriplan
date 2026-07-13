"""Cargador de la base curada de alimentos (Módulo 3).

Lee el CSV curado y produce FoodItems globales (tenant_id=None) con UUIDs
deterministas (uuid5 sobre name_es) para que la carga sea idempotente.

El CSV es la fuente de verdad versionada; los números NO se inventan aquí.
La columna `source_ref` guarda el `fdc_id` exacto de USDA FoodData Central, y
`usda_fdc.py` + el CLI `nutriplan-food` son los que lo enlazan y auditan. En
runtime no se consulta USDA: se lee este CSV y ya.
"""

import csv
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot, UnitGranularity

_NAMESPACE = uuid5(NAMESPACE_URL, "nutriplan/foods")


def food_id_for(name_es: str) -> UUID:
    return uuid5(_NAMESPACE, name_es.strip().lower())


def _text(row: dict[str, str], key: str) -> str:
    return (row.get(key) or "").strip()


def _number(row: dict[str, str], key: str) -> float | None:
    raw = _text(row, key)
    return float(raw) if raw else None


def _semicolons(row: dict[str, str], key: str) -> list[str]:
    return [part.strip() for part in _text(row, key).split(";") if part.strip()]


def load_curated_foods(csv_path: Path) -> list[FoodItem]:
    foods: list[FoodItem] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            name_es = _text(row, "name_es")

            # Las columnas de porción y afinidad son opcionales: si el CSV no las
            # trae, FoodItem las deriva de la categoría y la granularidad. Así un
            # catálogo viejo (o un alimento custom) sigue cargando.
            optional: dict[str, Any] = {}
            step = _number(row, "portion_step_g")
            if step is not None:
                optional["portion_step_g"] = step
            slots = _semicolons(row, "meal_slots")
            if slots:
                optional["meal_slots"] = [MealSlot(s) for s in slots]

            foods.append(
                FoodItem(
                    id=food_id_for(name_es),
                    tenant_id=None,  # alimento global compartido
                    source=_text(row, "source") or "USDA",
                    source_ref=_text(row, "source_ref") or None,
                    name_es=name_es,
                    name_en=_text(row, "name_en") or None,
                    category=FoodCategory(_text(row, "category")),
                    kcal_100g=float(row["kcal_100g"]),
                    protein_100g=float(row["protein_100g"]),
                    carb_100g=float(row["carb_100g"]),
                    fat_100g=float(row["fat_100g"]),
                    fiber_100g=_number(row, "fiber_100g") or 0.0,
                    tags=_semicolons(row, "tags"),
                    default_unit_g=_number(row, "default_unit_g"),
                    unit_granularity=UnitGranularity(
                        _text(row, "unit_granularity") or "grams"
                    ),
                    unit_name=_text(row, "unit_name") or None,
                    portion_min_g=_number(row, "portion_min_g"),
                    portion_max_g=_number(row, "portion_max_g"),
                    is_free=_text(row, "is_free").lower() == "true",
                    free_text=_text(row, "free_text") or None,
                    **optional,
                )
            )
    return foods
