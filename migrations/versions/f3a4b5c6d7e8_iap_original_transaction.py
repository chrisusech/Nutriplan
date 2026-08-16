"""original_transaction_id y unique en external_ref para IAP

Apple renueva y reembolsa por originalTransactionId. external_ref único
impide que dos POST de la misma transacción dupliquen semanas.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-08-15 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3a4b5c6d7e8"
down_revision: str | Sequence[str] | None = "e2f3a4b5c6d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "membership_grants",
        sa.Column("original_transaction_id", sa.String(length=120), nullable=True),
    )
    op.create_index(
        op.f("ix_membership_grants_original_transaction_id"),
        "membership_grants",
        ["original_transaction_id"],
    )
    op.create_index(
        "uq_membership_grants_external_ref",
        "membership_grants",
        ["external_ref"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_membership_grants_external_ref", table_name="membership_grants")
    op.drop_index(
        op.f("ix_membership_grants_original_transaction_id"),
        table_name="membership_grants",
    )
    op.drop_column("membership_grants", "original_transaction_id")
