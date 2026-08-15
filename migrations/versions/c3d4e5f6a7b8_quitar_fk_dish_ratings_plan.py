"""Quitar FK dish_ratings → plan_cycles

Las estrellas tienen que sobrevivir cuando se borra el borrador al regenerar.
Bases creadas con el esquema inicial aún tenían esa FK; el modelo ya no.

Revision ID: c3d4e5f6a7b8
Revises: a1b2c3d4e5f6
Create Date: 2026-08-04 23:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite no permite DROP CONSTRAINT: recreamos la tabla sin la FK al plan.
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
        # Índice UNIQUE con nombre: SQLite no refleja bien CONSTRAINT … UNIQUE
        # y Alembic lo veía como "falta dish_ratings_one_per_meal".
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
        op.execute(
            "CREATE INDEX ix_dish_ratings_template_id ON dish_ratings (template_id)"
        )
        op.execute("CREATE INDEX ix_dish_ratings_tenant_id ON dish_ratings (tenant_id)")
        op.execute("CREATE INDEX ix_dish_ratings_user_id ON dish_ratings (user_id)")
        op.execute("PRAGMA foreign_keys=ON")
        return

    # Postgres (y similares): quitar solo la FK del plan si existe.
    inspector = sa.inspect(bind)
    for fk in inspector.get_foreign_keys("dish_ratings"):
        if fk.get("referred_table") == "plan_cycles" and fk.get("name"):
            op.drop_constraint(fk["name"], "dish_ratings", type_="foreignkey")


def downgrade() -> None:
    # No reponemos la FK: volvería a romper la regeneración del menú.
    pass
