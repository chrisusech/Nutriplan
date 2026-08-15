"""weight_entries: pesaje semanal obligatorio

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-08-11 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "weight_entries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("weight_kg", sa.Float(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.String(length=400), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "week_start", name="weight_entries_client_week"),
    )
    op.create_index(op.f("ix_weight_entries_tenant_id"), "weight_entries", ["tenant_id"])
    op.create_index(op.f("ix_weight_entries_client_id"), "weight_entries", ["client_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_weight_entries_client_id"), table_name="weight_entries")
    op.drop_index(op.f("ix_weight_entries_tenant_id"), table_name="weight_entries")
    op.drop_table("weight_entries")
