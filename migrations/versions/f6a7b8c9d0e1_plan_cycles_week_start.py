"""plan_cycles.week_start: el menú pertenece a una semana ISO

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-12 12:20:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("plan_cycles", sa.Column("week_start", sa.Date(), nullable=True))
    # Los planes que ya existen pertenecen a la semana en que se crearon: el lunes
    # ISO de `created_at`. En SQLite date(created_at, 'weekday 0', '-6 days') da el
    # lunes; en Postgres lo hace date_trunc('week', ...).
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.execute(
            "UPDATE plan_cycles "
            "SET week_start = date(created_at, 'weekday 0', '-6 days') "
            "WHERE week_start IS NULL"
        )
    else:
        op.execute(
            "UPDATE plan_cycles "
            "SET week_start = date_trunc('week', created_at)::date "
            "WHERE week_start IS NULL"
        )
    with op.batch_alter_table("plan_cycles") as batch:
        batch.alter_column("week_start", existing_type=sa.Date(), nullable=False)
    op.create_index(
        op.f("ix_plan_cycles_week_start"), "plan_cycles", ["week_start"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_plan_cycles_week_start"), table_name="plan_cycles")
    op.drop_column("plan_cycles", "week_start")
