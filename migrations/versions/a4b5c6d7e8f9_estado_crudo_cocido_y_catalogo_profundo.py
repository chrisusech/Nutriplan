"""Estado crudo/cocido, factor de rendimiento y catálogo de dos niveles

Dos cosas que la tabla `foods` no sabía decir:

1. En qué estado están sus macros. Vivía en el sufijo del nombre («arroz blanco
   cocido»), así que la lista de compras pedía gramos cocidos — y nadie compra
   arroz cocido. Ahora `state` + `yield_factor` permiten volver al crudo.
2. Si la fila se LISTA o solo está VIVA. Al entrar el catálogo curado de USDA
   (miles de alimentos), enseñarlos todos en el picker del perfil no tiene
   sentido: `engine_default` separa los pocos cientos que se listan de los
   miles que solo se alcanzan buscándolos por nombre.

`engine_default` entra con server_default TRUE para que las filas que ya
existían sigan viéndose exactamente igual; es el importador el que marca en
FALSE lo que va al fondo del catálogo.

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-08-16 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4b5c6d7e8f9"
down_revision: str | Sequence[str] | None = "f3a4b5c6d7e8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("foods") as batch:
        batch.add_column(
            sa.Column(
                "state",
                sa.String(length=12),
                nullable=False,
                server_default="no_aplica",
            )
        )
        batch.add_column(sa.Column("cooking_method", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("yield_factor", sa.Float(), nullable=True))
        batch.add_column(sa.Column("raw_equivalent_id", sa.Uuid(), nullable=True))
        batch.add_column(
            sa.Column(
                "engine_default",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )
        batch.add_column(
            sa.Column("sugar_100g", sa.Float(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("sodium_mg_100g", sa.Float(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("fdc_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("curated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("curated_by", sa.String(length=40), nullable=True))
        batch.create_foreign_key(
            "fk_foods_raw_equivalent_id", "foods", ["raw_equivalent_id"], ["id"]
        )

    op.create_index("ix_foods_state", "foods", ["state"])
    op.create_index("ix_foods_fdc_id", "foods", ["fdc_id"])

    # `source_ref` ya guardaba el fdc_id como texto en las filas de USDA; se
    # copia al entero para que el importador pueda anclar sin adivinar.
    op.execute(
        """
        UPDATE foods
           SET fdc_id = CAST(source_ref AS INTEGER)
         WHERE source = 'USDA'
           AND source_ref IS NOT NULL
           AND source_ref <> ''
        """
    )


def downgrade() -> None:
    op.drop_index("ix_foods_fdc_id", table_name="foods")
    op.drop_index("ix_foods_state", table_name="foods")
    with op.batch_alter_table("foods") as batch:
        batch.drop_constraint("fk_foods_raw_equivalent_id", type_="foreignkey")
        for column in (
            "curated_by",
            "curated_at",
            "fdc_id",
            "sodium_mg_100g",
            "sugar_100g",
            "engine_default",
            "raw_equivalent_id",
            "yield_factor",
            "cooking_method",
            "state",
        ):
            batch.drop_column(column)
