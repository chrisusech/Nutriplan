"""device_tokens para push nativo (beta)

Revision ID: a1b2c3d4e5f6
Revises: 87669eb0db17
Create Date: 2026-08-03 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "87669eb0db17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("token", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "token", name="device_tokens_user_token"),
    )
    op.create_index(op.f("ix_device_tokens_tenant_id"), "device_tokens", ["tenant_id"])
    op.create_index(op.f("ix_device_tokens_user_id"), "device_tokens", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_device_tokens_user_id"), table_name="device_tokens")
    op.drop_index(op.f("ix_device_tokens_tenant_id"), table_name="device_tokens")
    op.drop_table("device_tokens")
