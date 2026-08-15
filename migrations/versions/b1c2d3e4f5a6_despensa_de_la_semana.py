"""Lo que ya tiene en casa esta semana

Tabla propia y no un tercer estado de `client_food_preferences` porque contesta
otra pregunta y caduca: las preferencias dicen "esto lo como", esto dice "esto lo
tengo en la nevera ahora". El `week_start` la vacía sola cada lunes.

Revision ID: b1c2d3e4f5a6
Revises: 2a1dcbf4afe1
Create Date: 2026-08-13 15:52:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c2d3e4f5a6"
down_revision: str | Sequence[str] | None = "2a1dcbf4afe1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_pantry_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("food_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "food_id", "week_start"),
    )
    op.create_index(
        op.f("ix_client_pantry_items_tenant_id"), "client_pantry_items", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_client_pantry_items_client_id"), "client_pantry_items", ["client_id"]
    )
    op.create_index(
        op.f("ix_client_pantry_items_week_start"), "client_pantry_items", ["week_start"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_client_pantry_items_week_start"), table_name="client_pantry_items")
    op.drop_index(op.f("ix_client_pantry_items_client_id"), table_name="client_pantry_items")
    op.drop_index(op.f("ix_client_pantry_items_tenant_id"), table_name="client_pantry_items")
    op.drop_table("client_pantry_items")
