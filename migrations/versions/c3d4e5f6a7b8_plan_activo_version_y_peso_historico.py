"""el plan definitivo, sus versiones, y con que peso se calculo cada una

Se podian generar varios planes por cliente, pero no decir CUAL era el bueno. La
pantalla /planes solo mostraba el mas reciente y los anteriores quedaban huerfanos
(seguian en la base; no habia puerta para entrar a ellos). Sin eso no hay
seguimiento: no se puede cerrar un mes, ajustar los macros y comparar.

Tres columnas:

- `clients.active_plan_id`: cual de sus planes es EL plan. Un puntero, no una
  bandera por fila: "uno solo activo" lo garantiza la cardinalidad de la columna, no
  codigo que haya que acordarse de escribir. Archivar es repuntar; volver a la v2,
  tambien. Sin FK a proposito (seria un ciclo clients <-> plan_cycles).
- `plan_cycles.version`: el numero humano ("Plan nutricional v3").
- `nutrition_targets.weight_kg`: CON QUE PESO se calcularon esos macros.
  `clients.weight_kg` es mutable, asi que al registrar el peso del mes siguiente se
  perdia el del anterior — y con el, la unica forma de saber si el deficit
  funcionaba. La tabla ya era append-only: el historial de macros existia, solo le
  faltaba el peso.

El backfill no deja a nadie huerfano: cada cliente activa su plan mas reciente, las
versiones se numeran por fecha, y los objetivos heredan el peso actual del cliente
(que es el unico que se conoce).

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("plan_cycles") as batch:
        batch.add_column(
            sa.Column("version", sa.SmallInteger(), nullable=False, server_default="1")
        )
    with op.batch_alter_table("clients") as batch:
        batch.add_column(sa.Column("active_plan_id", sa.Uuid(), nullable=True))
    with op.batch_alter_table("nutrition_targets") as batch:
        batch.add_column(sa.Column("weight_kg", sa.Float(), nullable=True))

    bind = op.get_bind()

    # El peso con el que se calcularon: el unico que conocemos es el actual del
    # cliente. Para los objetivos ya guardados es la mejor verdad disponible.
    bind.execute(
        sa.text(
            "UPDATE nutrition_targets SET weight_kg = ("
            "  SELECT c.weight_kg FROM clients c WHERE c.id = nutrition_targets.client_id"
            ")"
        )
    )

    # Las versiones, numeradas por fecha dentro de cada cliente. En Python y no con
    # una window function: se comporta igual en SQLite y en Postgres.
    rows = bind.execute(
        sa.text(
            "SELECT id, client_id FROM plan_cycles ORDER BY client_id, created_at, id"
        )
    ).fetchall()
    seen: dict[object, int] = {}
    for plan_id, client_id in rows:
        seen[client_id] = seen.get(client_id, 0) + 1
        bind.execute(
            sa.text("UPDATE plan_cycles SET version = :v WHERE id = :id"),
            {"v": seen[client_id], "id": plan_id},
        )

    # Y el activo de cada cliente: el mas reciente, que es el que la pantalla venia
    # enseñando. Nadie cambia de plan por migrar.
    bind.execute(
        sa.text(
            "UPDATE clients SET active_plan_id = ("
            "  SELECT p.id FROM plan_cycles p WHERE p.client_id = clients.id"
            "  ORDER BY p.created_at DESC, p.id DESC LIMIT 1"
            ")"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("nutrition_targets") as batch:
        batch.drop_column("weight_kg")
    with op.batch_alter_table("clients") as batch:
        batch.drop_column("active_plan_id")
    with op.batch_alter_table("plan_cycles") as batch:
        batch.drop_column("version")
