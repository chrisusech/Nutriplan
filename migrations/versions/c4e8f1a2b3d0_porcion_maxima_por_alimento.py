"""porcion maxima por alimento

Revision ID: c4e8f1a2b3d0
Revises: 6ba1ae88a2d4
Create Date: 2026-07-13 13:30:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4e8f1a2b3d0"
down_revision: Union[str, Sequence[str], None] = "6ba1ae88a2d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("foods", schema=None) as batch_op:
        batch_op.add_column(sa.Column("portion_max_g", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("foods", schema=None) as batch_op:
        batch_op.drop_column("portion_max_g")
