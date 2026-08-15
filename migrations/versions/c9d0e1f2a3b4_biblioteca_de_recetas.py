"""Biblioteca de recetas: de qué está hecha, cuánto alimenta y qué tal salió

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-08-12 13:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Las recetas que ya están en caché no tienen de dónde sacar sus alimentos
    # ni sus macros: se rellenan solas la próxima vez que el plato se resuelve.
    op.add_column(
        "dish_recipes",
        sa.Column("food_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.add_column(
        "dish_recipes",
        sa.Column("reference_grams", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.add_column("dish_recipes", sa.Column("macros", sa.JSON(), nullable=True))
    op.add_column("dish_recipes", sa.Column("rating_avg", sa.Float(), nullable=True))
    op.add_column(
        "dish_recipes",
        sa.Column("rating_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "dish_recipes",
        sa.Column("times_served", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "dish_recipes",
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_dish_recipes_rating_avg", "dish_recipes", ["rating_avg"])


def downgrade() -> None:
    op.drop_index("ix_dish_recipes_rating_avg", table_name="dish_recipes")
    for name in (
        "retired_at", "times_served", "rating_count", "rating_avg",
        "macros", "reference_grams", "food_ids",
    ):
        op.drop_column("dish_recipes", name)
