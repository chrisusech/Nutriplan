"""Marcar lo comido en cada comida del día

El anillo de la semana cuenta kcal consumidas, no el menú entero. Un booleano
en la fila de la comida basta: al regenerar la semana las filas son nuevas y
el conteo arranca en cero.

Revision ID: c0d1e2f3a4b5
Revises: b1c2d3e4f5a6
Create Date: 2026-08-14 11:55:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c0d1e2f3a4b5"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meal_entries",
        sa.Column("eaten", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("meal_entries", "eaten")
