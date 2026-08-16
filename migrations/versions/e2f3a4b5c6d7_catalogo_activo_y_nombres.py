"""Catálogo activo: remap de nombres y retiro de lo que el CSV ya no trae

Renombrar un alimento cambia su PK (uuid5 del name_es). El seed solo hace
upsert, así que en beta convivían tofu firme/tofu, las dos proteínas y las
dos arepas, y los granos retirados seguían en el picker. Esta migración
reescribe las FKs al id nuevo, marca fuera del universo lo que el CSV ya no
lista, y deja la fila para que un plato viejo siga teniendo nombre.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-08-15 14:00:00.000000
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op

# Copiado a propósito y no importado: esta migración tiene que poder correr
# dentro de diez años, y el módulo que lo definía (`curated_loader`) ya no
# existe — el catálogo dejó de venir de un CSV. Una migración congela el mundo
# tal como era cuando se escribió.
_LEGACY_NAMESPACE = uuid5(NAMESPACE_URL, "nutriplan/foods")


def food_id_for(name_es: str) -> UUID:
    return uuid5(_LEGACY_NAMESPACE, name_es.strip().lower())

revision: str = "e2f3a4b5c6d7"
down_revision: str | Sequence[str] | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RENAMES: tuple[tuple[str, str], ...] = (
    ("proteína en polvo whey", "proteína en polvo"),
    ("tofu firme", "tofu"),
    ("arepa de maíz", "arepa Sarys extradélgada"),
)
_RETIRED: tuple[str, ...] = (
    "cuscús cocido",
    "tahini",
    "huevo de codorniz",
    "tempeh",
    "frijol cargamanto",
    "cebada perlada cocida",
    "bulgur cocido",
    "trigo sarraceno cocido",
    "amaranto cocido",
    "mijo cocido",
)
_FK_UNIQUE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("client_food_preferences", ("client_id",)),
    ("client_food_bans", ("client_id",)),
    ("client_pantry_items", ("client_id", "week_start")),
)
_JSON_LISTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("client_taste_signals", ("avoid_food_ids", "prefer_food_ids")),
    ("dish_recipes", ("food_ids",)),
)


def upgrade() -> None:
    with op.batch_alter_table("foods") as batch:
        batch.add_column(
            sa.Column(
                "catalog_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
    conn = op.get_bind()
    mapping = {food_id_for(old): food_id_for(new) for old, new in _RENAMES}
    for old_id, new_id in mapping.items():
        _ensure_new_row(conn, old_id, new_id)
        _remap_unique(conn, old_id, new_id)
        _run(
            conn,
            "UPDATE meal_items SET food_id = :new WHERE food_id = :old",
            new=new_id,
            old=old_id,
        )
        _rewrite_json(conn, {str(old_id): str(new_id)})
        _run(conn, "DELETE FROM foods WHERE id = :old", old=old_id)
    for name in _RETIRED:
        _run(
            conn,
            "UPDATE foods SET catalog_active = false WHERE id = :id AND tenant_id IS NULL",
            id=food_id_for(name),
        )


def downgrade() -> None:
    with op.batch_alter_table("foods") as batch:
        batch.drop_column("catalog_active")


def _run(conn: sa.Connection, sql: str, **params: object) -> sa.Result[object]:
    """SQLite no bindea UUID nativo: CHAR(32) hex. Postgres sí."""
    bound: dict[str, object] = {}
    for key, value in params.items():
        if isinstance(value, UUID) and conn.dialect.name == "sqlite":
            bound[key] = value.hex
        else:
            bound[key] = value
    return conn.execute(sa.text(sql), bound)


def _ensure_new_row(conn: sa.Connection, old_id: UUID, new_id: UUID) -> None:
    """Si el id nuevo aún no existe, copia la fila vieja para no romper FKs."""
    exists = _run(conn, "SELECT 1 FROM foods WHERE id = :id", id=new_id).first()
    if exists is not None:
        return
    row = _run(conn, "SELECT * FROM foods WHERE id = :id", id=old_id).mappings().first()
    if row is None:
        return
    data = dict(row)
    data["id"] = new_id
    for key, value in list(data.items()):
        if isinstance(value, (list, dict)):
            data[key] = json.dumps(value)
        elif isinstance(value, UUID) and conn.dialect.name == "sqlite":
            data[key] = value.hex
    cols = ", ".join(data)
    params = ", ".join(f":{c}" for c in data)
    conn.execute(sa.text(f"INSERT INTO foods ({cols}) VALUES ({params})"), data)


def _remap_unique(conn: sa.Connection, old_id: UUID, new_id: UUID) -> None:
    for table, keys in _FK_UNIQUE:
        key_sql = ", ".join(keys)
        rows = _run(
            conn, f"SELECT id, {key_sql} FROM {table} WHERE food_id = :old", old=old_id
        ).mappings()
        for row in rows:
            clause = " AND ".join(f"{k} = :{k}" for k in keys)
            clash = _run(
                conn,
                f"SELECT id FROM {table} WHERE {clause} AND food_id = :new",
                **{k: row[k] for k in keys},
                new=new_id,
            ).first()
            if clash is not None:
                _run(conn, f"DELETE FROM {table} WHERE id = :id", id=row["id"])
            else:
                _run(
                    conn,
                    f"UPDATE {table} SET food_id = :new WHERE id = :id",
                    new=new_id,
                    id=row["id"],
                )


def _rewrite_json(conn: sa.Connection, mapping: dict[str, str]) -> None:
    for table, columns in _JSON_LISTS:
        inspect = sa.inspect(conn)
        if not inspect.has_table(table):
            continue
        col_sql = ", ".join(columns)
        rows = conn.execute(sa.text(f"SELECT id, {col_sql} FROM {table}")).mappings()
        for row in rows:
            patch: dict[str, object] = {}
            for col in columns:
                rewritten = _map_ids(row[col], mapping)
                if rewritten is not None:
                    patch[col] = rewritten
            if not patch:
                continue
            sets = ", ".join(f"{c} = :{c}" for c in patch)
            conn.execute(
                sa.text(f"UPDATE {table} SET {sets} WHERE id = :id"),
                {**patch, "id": row["id"]},
            )


def _map_ids(raw: object, mapping: dict[str, str]) -> str | None:
    if raw is None:
        return None
    values = raw if isinstance(raw, list) else json.loads(raw or "[]")
    if not isinstance(values, list):
        return None
    updated = [mapping.get(str(v), str(v)) for v in values]
    if updated == [str(v) for v in values]:
        return None
    return json.dumps(updated)
