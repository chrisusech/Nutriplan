"""alias de alimentos: el termino del intake -> el alimento canonico

El intake llega en texto libre. Sin alias, "pollo" lo resolvia el fuzzy, que da
el mismo bono de palabra completa a "pechuga de pollo" y a "muslo de pollo" y
desempataba por el ORDEN DE ITERACION del catalogo: el mismo cliente acababa con
pechuga si el catalogo venia del CSV y con muslo si venia de la base. El alias es
la eleccion explicita del entrenador sobre cual es el alimento por defecto de un
termino comun.

Revision ID: e7a1c2d3b4f5
Revises: d5f6a7b8c9d0
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7a1c2d3b4f5"
down_revision: str | None = "d5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("foods") as batch:
        batch.add_column(
            sa.Column("aliases", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("foods") as batch:
        batch.drop_column("aliases")
