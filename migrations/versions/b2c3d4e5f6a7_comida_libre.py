"""la comida libre de la semana

Existia como una frase suelta en el PDF ("puedes hacerla el sabado o el domingo")
que salia en TODOS los planes, tuvieran o no comida libre. Ahora es un dato: el
entrenador elige dia y comida, y esa celda sale sin alimentos ni gramos.

Sus macros no se cuentan. Las demas comidas de ese dia conservan su objetivo de
siempre y el dia suma por debajo, que es lo que significa comerse una pizza el
domingo (ver `macro_split.daily_minus_free_meal`).

La preferencia vive en el CLIENTE (se elige antes de generar y entra en el hash de
entrada); el reflejo dentro del plan es `meal_entries.is_free_meal`, para que un
plan ya exportado siga diciendo lo que decia aunque manana el cliente cambie de dia.

NULL / false = no hay comida libre = el mundo de hoy.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("free_meal_day", sa.SmallInteger(), nullable=True))
        batch.add_column(sa.Column("free_meal_slot", sa.String(20), nullable=True))
    with op.batch_alter_table("meal_entries") as batch:
        batch.add_column(
            sa.Column(
                "is_free_meal",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("meal_entries") as batch:
        batch.drop_column("is_free_meal")
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("free_meal_slot")
        batch.drop_column("free_meal_day")
