"""Cierre de semana: comentario de la persona y señales de gusto

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-12 12:45:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # `note` la escribe la IA; esto lo escribe la persona.
    op.add_column(
        "weight_entries", sa.Column("client_comment", sa.Text(), nullable=True)
    )
    op.create_table(
        "client_taste_signals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("avoid_food_ids", sa.JSON(), nullable=False),
        sa.Column("prefer_food_ids", sa.JSON(), nullable=False),
        sa.Column("adjustments", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("model", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "client_id", "week_start", name="taste_signals_client_week"
        ),
    )
    op.create_index(
        op.f("ix_client_taste_signals_tenant_id"), "client_taste_signals", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_client_taste_signals_client_id"), "client_taste_signals", ["client_id"]
    )
    op.create_index(
        op.f("ix_client_taste_signals_week_start"),
        "client_taste_signals",
        ["week_start"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_client_taste_signals_week_start"), table_name="client_taste_signals"
    )
    op.drop_index(
        op.f("ix_client_taste_signals_client_id"), table_name="client_taste_signals"
    )
    op.drop_index(
        op.f("ix_client_taste_signals_tenant_id"), table_name="client_taste_signals"
    )
    op.drop_table("client_taste_signals")
    op.drop_column("weight_entries", "client_comment")
