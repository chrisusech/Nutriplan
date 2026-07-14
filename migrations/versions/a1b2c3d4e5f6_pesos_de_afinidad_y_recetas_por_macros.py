"""pesos de afinidad por comida, y recetas que declaran sus comidas

La afinidad era binaria: el arroz y la arepa "van" los dos en un almuerzo, asi
que el motor los tomaba por equivalentes y salia arepa a mediodia y pan en la
cena. `foods.slot_weights` le da un ORDEN ({"almuerzo": 3}), sin prohibir nada:
quien solo tiene arepa sigue comiendo arepa al mediodia.

`{}` = todo al peso por defecto = exactamente el comportamiento de hoy, asi que
las 172 filas del catalogo siguen valiendo sin tocarlas (y `seed_local` las
reescribe desde el CSV en cada arranque).

`recipes.meal_slots` es para el otro lado del mismo problema: una receta por
macros (la hamburguesa de un restaurante) se materializa como alimento compuesto,
y sin declarar sus comidas FoodItem se las derivaba de la CATEGORIA — una
hamburguesa acababa siendo apta para desayuno.

Revision ID: a1b2c3d4e5f6
Revises: f2b3c4d5e6a7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "f2b3c4d5e6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("foods") as batch:
        batch.add_column(
            sa.Column("slot_weights", sa.JSON(), nullable=False, server_default="{}")
        )
    with op.batch_alter_table("recipes") as batch:
        batch.add_column(
            sa.Column("meal_slots", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("recipes") as batch:
        batch.drop_column("meal_slots")
    with op.batch_alter_table("foods") as batch:
        batch.drop_column("slot_weights")
