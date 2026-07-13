"""client_food_bans para edición quirúrgica (Fase 6)

Revision ID: d5f6a7b8c9d0
Revises: c4e8f1a2b3d0
Create Date: 2026-07-13 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "c4e8f1a2b3d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "client_food_bans",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("food_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("client_id", "food_id"),
    )
    op.create_index("ix_client_food_bans_tenant_id", "client_food_bans", ["tenant_id"])
    op.create_index("ix_client_food_bans_client_id", "client_food_bans", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_client_food_bans_client_id", table_name="client_food_bans")
    op.drop_index("ix_client_food_bans_tenant_id", table_name="client_food_bans")
    op.drop_table("client_food_bans")
