"""las comidas que hace el cliente: 5 por defecto, pero hay quien come 4

El plan siempre tenia cinco comidas porque el 5 estaba clavado en el codigo. Un
cliente que come cuatro no tenia forma de existir. Ahora la ficha del cliente
declara CUALES come, y el reparto de macros se renormaliza sobre esas
(`NutritionConfig.for_slots`).

NULL = las cinco de siempre: los clientes ya creados no cambian de plan.

Revision ID: f2b3c4d5e6a7
Revises: e7a1c2d3b4f5
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2b3c4d5e6a7"
down_revision: str | None = "e7a1c2d3b4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("meal_slots", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("meal_slots")
