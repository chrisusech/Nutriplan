"""Carga del catálogo USDA curado

Mete en `foods` los alimentos curados desde USDA FoodData Central. Se ejecuta
UNA vez (es una migración) y a partir de ahí la base es la fuente de verdad: la
consola escribe encima y nada la vuelve a sobrescribir.

`data/foods/catalogo.jsonl` es un artefacto de build — la salida de
`nutriplan-food filter | curate | validate`. Existe porque las miles de filas
tienen que llegar a Supabase de alguna forma; no se edita a mano y nadie lo lee
en runtime.

Lo que ya estaba en la tabla NO se toca. Un candidato se descarta si su `fdc_id`
o su nombre normalizado ya existen: los alimentos que la nutricionista curó a
mano mandan sobre el volcado automático, y sus ids siguen siendo los mismos, así
que ninguna preferencia, veto, despensa ni plato viejo se rompe.

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-16 11:00:00.000000
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "b5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FOODS_DIR = Path(__file__).resolve().parents[2] / "data" / "foods"

# El orden importa: primero el catálogo curado a mano, que es el que manda sobre
# los nombres (las plantillas de platos referencian varios por su `name_es`
# exacto) y el que conserva los ids con los que ya hay preferencias y platos
# guardados. El volcado de USDA entra después y cede ante cualquier choque.
CATALOG_PATHS = (_FOODS_DIR / "catalogo_base.jsonl", _FOODS_DIR / "catalogo.jsonl")

_COLUMNS = (
    "id",
    "tenant_id",
    "source",
    "source_ref",
    "fdc_id",
    "name_es",
    "name_norm",
    "name_en",
    "category",
    "kcal_100g",
    "protein_100g",
    "carb_100g",
    "fat_100g",
    "fiber_100g",
    "sugar_100g",
    "sodium_mg_100g",
    "tags",
    "aliases",
    "default_unit_g",
    "unit_granularity",
    "unit_name",
    "portion_step_g",
    "portion_min_g",
    "portion_max_g",
    "meal_slots",
    "slot_weights",
    "is_free",
    "free_text",
    "catalog_active",
    "state",
    "cooking_method",
    "yield_factor",
    "raw_equivalent_id",
    "engine_default",
    "curated_by",
)

# Marca de origen: es lo que permite que el downgrade sepa qué filas trajo esta
# carga y no se lleve por delante lo que curó una persona.
_STAMP = "catalogo-usda"


def upgrade() -> None:
    conn = op.get_bind()
    sqlite = conn.dialect.name == "sqlite"
    taken_ids, taken_fdc, taken_names = _existing(conn)

    rows: list[dict[str, object]] = []
    for path in CATALOG_PATHS:
        # Sin artefacto no hay nada que cargar: se sigue con el siguiente. Un
        # despliegue no se puede caer por un archivo que solo se genera cuando
        # alguien reconstruye el catálogo desde el bulk de USDA.
        if not path.is_file():
            continue
        for entry in _read_catalog(path):
            food = _to_row(entry)
            fdc_id, name_norm, food_id = food["fdc_id"], food["name_norm"], str(food["id"])
            if food_id in taken_ids or name_norm in taken_names:
                continue
            # El ancla USDA solo desempata el volcado automático. En el catálogo
            # curado dos filas pueden compartir `fdc_id` a propósito: la leche
            # deslactosada son los macros de la leche con lactasa añadida, y no
            # por eso deja de ser otro alimento en la despensa de alguien.
            derived = entry.get("id_override") is not None
            if fdc_id is not None and fdc_id in taken_fdc and not derived:
                continue
            taken_ids.add(food_id)
            taken_names.add(name_norm)
            if fdc_id is not None:
                taken_fdc.add(fdc_id)
            if sqlite:
                food["id"] = UUID(food_id).hex
            rows.append(food)

    if not rows:
        return
    columns = ", ".join(_COLUMNS)
    params = ", ".join(f":{c}" for c in _COLUMNS)
    conn.execute(sa.text(f"INSERT INTO foods ({columns}) VALUES ({params})"), rows)


def downgrade() -> None:
    # Solo se van los que trajo el volcado: lo curado a mano no lleva fdc_id
    # propio de esta carga y se queda donde está.
    op.get_bind().execute(
        sa.text("DELETE FROM foods WHERE curated_by = :stamp"), {"stamp": _STAMP}
    )


def _existing(conn: sa.Connection) -> tuple[set[str], set[object], set[object]]:
    """Lo que ya hay en la tabla: ids, anclas USDA y nombres normalizados."""
    rows = conn.execute(sa.text("SELECT id, fdc_id, name_norm FROM foods")).all()
    return (
        {str(UUID(str(r[0]))) for r in rows},
        {r[1] for r in rows if r[1] is not None},
        {r[2] for r in rows},
    )


def _read_catalog(path: Path) -> list[dict[str, object]]:
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _to_row(entry: dict[str, object]) -> dict[str, object]:
    """Traduce una fila del artefacto a columnas de `foods`.

    Se importan aquí y no arriba para que el módulo siga cargando aunque el
    paquete cambie de forma: una migración vieja tiene que poder correr siempre.
    """
    from nutriplan.adapters.food.catalog import CatalogEntry, to_food_item
    from nutriplan.domain.food_matching import normalize

    food = to_food_item(CatalogEntry.model_validate(entry))
    return {
        "id": str(food.id),
        "tenant_id": None,
        "source": food.source,
        "source_ref": food.source_ref,
        "fdc_id": food.fdc_id,
        "name_es": food.name_es,
        "name_norm": normalize(food.name_es),
        "name_en": food.name_en,
        "category": food.category.value,
        "kcal_100g": food.kcal_100g,
        "protein_100g": food.protein_100g,
        "carb_100g": food.carb_100g,
        "fat_100g": food.fat_100g,
        "fiber_100g": food.fiber_100g,
        "sugar_100g": entry.get("sugar_100g") or 0.0,
        "sodium_mg_100g": entry.get("sodium_mg_100g") or 0.0,
        "tags": json.dumps(list(food.tags)),
        "aliases": json.dumps(list(food.aliases)),
        "default_unit_g": food.default_unit_g,
        "unit_granularity": food.unit_granularity.value,
        "unit_name": food.unit_name,
        "portion_step_g": food.portion_step_g,
        "portion_min_g": food.portion_min_g,
        "portion_max_g": food.portion_max_g,
        "meal_slots": json.dumps([s.value for s in food.meal_slots]),
        "slot_weights": json.dumps({s.value: w for s, w in food.slot_weights.items()}),
        "is_free": food.is_free,
        "free_text": food.free_text,
        "catalog_active": True,
        "state": food.state.value,
        "cooking_method": food.cooking_method.value if food.cooking_method else None,
        "yield_factor": food.yield_factor,
        "raw_equivalent_id": None,
        "engine_default": food.engine_default,
        "curated_by": _STAMP,
    }
