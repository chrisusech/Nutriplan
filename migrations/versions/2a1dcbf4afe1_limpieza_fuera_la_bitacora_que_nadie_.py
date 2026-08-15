"""Limpieza: fuera la bitácora que nadie escribía y las columnas sin cablear

`audit_log` se creó con el esquema inicial y nunca recibió una sola fila: el
repositorio existía, estaba cableado en el contenedor, y ningún caso de uso lo
llamaba. Lo mismo con `dish_ratings.would_repeat` y `dish_recipes.verified_at`,
declaradas y jamás escritas ni leídas.

De paso se cambian índices por los que las pantallas usan de verdad: el de
`input_hash` suelto sobra (el unique de (tenant_id, input_hash, variant) ya
cubre la única búsqueda, que siempre lleva el tenant delante), y faltaban los
compuestos de "los planes de esta persona en esta semana" y "los últimos
targets de esta persona".

Revision ID: 2a1dcbf4afe1
Revises: c9d0e1f2a3b4
Create Date: 2026-08-12 20:28:21.396462
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import sqlite

revision: str = "2a1dcbf4afe1"
down_revision: str | Sequence[str] | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("audit_log")

    with op.batch_alter_table("dish_ratings") as batch:
        batch.drop_column("would_repeat")
    with op.batch_alter_table("dish_recipes") as batch:
        batch.drop_column("verified_at")

    with op.batch_alter_table("plan_cycles") as batch:
        batch.drop_index(batch.f("ix_plan_cycles_input_hash"))
        batch.create_index(
            "ix_plan_cycles_client_week", ["tenant_id", "client_id", "week_start"]
        )
    with op.batch_alter_table("nutrition_targets") as batch:
        batch.create_index(
            "ix_nutrition_targets_client_at", ["tenant_id", "client_id", "computed_at"]
        )


def downgrade() -> None:
    with op.batch_alter_table("nutrition_targets") as batch:
        batch.drop_index("ix_nutrition_targets_client_at")
    with op.batch_alter_table("plan_cycles") as batch:
        batch.drop_index("ix_plan_cycles_client_week")
        batch.create_index(batch.f("ix_plan_cycles_input_hash"), ["input_hash"])

    with op.batch_alter_table("dish_recipes") as batch:
        batch.add_column(sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("dish_ratings") as batch:
        batch.add_column(sa.Column("would_repeat", sa.Boolean(), nullable=True))

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=60), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("details", sqlite.JSON(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_audit_log_tenant_id"), "audit_log", ["tenant_id"])
