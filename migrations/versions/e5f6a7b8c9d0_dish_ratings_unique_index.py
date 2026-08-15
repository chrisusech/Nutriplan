"""Alinear unique de dish_ratings como índice con nombre

SQLite refleja CONSTRAINT UNIQUE como autoindex anónimo; el modelo usa
Index(..., unique=True) llamado dish_ratings_one_per_meal. Bases que ya
pasaron por c3d4e5f6a7b8 con CONSTRAINT se recrean aquí.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-12 10:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if bind.dialect.name == "sqlite":
        indexes = {ix["name"] for ix in inspector.get_indexes("dish_ratings")}
        if "dish_ratings_one_per_meal" in indexes:
            return

        # Recrear con el índice UNIQUE nombrado (sin CONSTRAINT inline).
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute(
            """
            CREATE TABLE dish_ratings__new (
                id INTEGER NOT NULL PRIMARY KEY,
                tenant_id CHAR(32) NOT NULL,
                user_id CHAR(32) NOT NULL,
                plan_cycle_id CHAR(32) NOT NULL,
                day_index SMALLINT NOT NULL,
                slot VARCHAR(20) NOT NULL,
                template_id VARCHAR(60),
                dish_key VARCHAR(64),
                rating SMALLINT NOT NULL,
                would_repeat BOOLEAN,
                comment TEXT,
                created_at DATETIME NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users (id)
            )
            """
        )
        op.execute(
            """
            INSERT INTO dish_ratings__new (
                id, tenant_id, user_id, plan_cycle_id, day_index, slot,
                template_id, dish_key, rating, would_repeat, comment, created_at
            )
            SELECT
                id, tenant_id, user_id, plan_cycle_id, day_index, slot,
                template_id, dish_key, rating, would_repeat, comment, created_at
            FROM dish_ratings
            """
        )
        op.execute("DROP TABLE dish_ratings")
        op.execute("ALTER TABLE dish_ratings__new RENAME TO dish_ratings")
        op.execute(
            """
            CREATE UNIQUE INDEX dish_ratings_one_per_meal
            ON dish_ratings (plan_cycle_id, day_index, slot)
            """
        )
        op.execute("CREATE INDEX ix_dish_ratings_created_at ON dish_ratings (created_at)")
        op.execute("CREATE INDEX ix_dish_ratings_dish_key ON dish_ratings (dish_key)")
        op.execute(
            "CREATE INDEX ix_dish_ratings_plan_cycle_id ON dish_ratings (plan_cycle_id)"
        )
        op.execute("CREATE INDEX ix_dish_ratings_template_id ON dish_ratings (template_id)")
        op.execute("CREATE INDEX ix_dish_ratings_tenant_id ON dish_ratings (tenant_id)")
        op.execute("CREATE INDEX ix_dish_ratings_user_id ON dish_ratings (user_id)")
        op.execute("PRAGMA foreign_keys=ON")
        return

    # Postgres: UniqueConstraint → unique Index con el mismo nombre.
    uq_names = {
        u.get("name") for u in inspector.get_unique_constraints("dish_ratings")
    }
    ix_names = {ix["name"] for ix in inspector.get_indexes("dish_ratings")}
    if "dish_ratings_one_per_meal" in uq_names:
        op.drop_constraint(
            "dish_ratings_one_per_meal", "dish_ratings", type_="unique"
        )
    if "dish_ratings_one_per_meal" not in ix_names:
        op.create_index(
            "dish_ratings_one_per_meal",
            "dish_ratings",
            ["plan_cycle_id", "day_index", "slot"],
            unique=True,
        )


def downgrade() -> None:
    pass
