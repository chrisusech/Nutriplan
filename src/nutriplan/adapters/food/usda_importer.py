"""Importador de la base curada de alimentos (Módulo 3).

Lee el CSV curado y produce FoodItems globales (tenant_id=None) con UUIDs
deterministas (uuid5 sobre name_es) para que la carga sea idempotente.
No se consulta la API de USDA en caliente durante la generación.
"""

import csv
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from nutriplan.domain.models import FoodCategory, FoodItem

_NAMESPACE = uuid5(NAMESPACE_URL, "nutriplan/foods")


def food_id_for(name_es: str) -> UUID:
    return uuid5(_NAMESPACE, name_es.strip().lower())


def load_curated_foods(csv_path: Path) -> list[FoodItem]:
    foods: list[FoodItem] = []
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            name_es = row["name_es"].strip()
            foods.append(
                FoodItem(
                    id=food_id_for(name_es),
                    tenant_id=None,  # alimento global compartido
                    source=row["source"].strip() or "USDA",
                    source_ref=row["source_ref"].strip() or None,
                    name_es=name_es,
                    name_en=row["name_en"].strip() or None,
                    category=FoodCategory(row["category"].strip()),
                    kcal_100g=float(row["kcal_100g"]),
                    protein_100g=float(row["protein_100g"]),
                    carb_100g=float(row["carb_100g"]),
                    fat_100g=float(row["fat_100g"]),
                    tags=[t for t in row["tags"].split(";") if t.strip()],
                    default_unit_g=float(row["default_unit_g"]) if row["default_unit_g"] else None,
                )
            )
    return foods
