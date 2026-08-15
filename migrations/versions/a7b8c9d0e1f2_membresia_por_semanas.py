"""membership_grants: la cuota deja de ser un número y pasa a ser un saldo

El override `users.max_menus` decía "cuántos menús en total, para siempre". El
negocio necesita "cuántas semanas y hasta cuándo", así que cada valor que había
se convierte en una concesión manual equivalente y la columna desaparece.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-12 12:35:00.000000
"""

import uuid
from datetime import UTC, datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "membership_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("weeks", sa.SmallInteger(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("granted_by", sa.Uuid(), nullable=True),
        sa.Column("external_ref", sa.String(length=120), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_membership_grants_tenant_id"), "membership_grants", ["tenant_id"]
    )
    op.create_index(
        op.f("ix_membership_grants_user_id"), "membership_grants", ["user_id"]
    )
    op.create_index(
        op.f("ix_membership_grants_source"), "membership_grants", ["source"]
    )

    _seed_grants_from_existing_accounts()

    with op.batch_alter_table("users") as batch:
        batch.drop_column("max_menus")


def _seed_grants_from_existing_accounts() -> None:
    """Nadie del BETA se queda sin poder generar por culpa de la migración.

    Quien tenía `max_menus` conserva ese número como semanas manuales sin
    caducidad; el resto recibe las semanas que la regla del BETA le daba (dos si
    ya había calificado platos, una si no). Sin caducidad a propósito: cobrarles
    retroactivamente una fecha que nunca aceptaron sería tramposo.
    """
    bind = op.get_bind()
    users = bind.execute(
        sa.text(
            "SELECT id, tenant_id, max_menus FROM users "
            "WHERE role = 'user' AND deleted_at IS NULL"
        )
    ).fetchall()
    if not users:
        return
    now = datetime.now(UTC)
    rows = []
    for user_id, tenant_id, max_menus in users:
        if max_menus is None:
            rated = bind.execute(
                sa.text("SELECT COUNT(*) FROM dish_ratings WHERE user_id = :uid"),
                {"uid": user_id},
            ).scalar()
            weeks = 2 if int(rated or 0) >= 5 else 1
            note = "Saldo heredado de la regla del BETA"
        else:
            weeks = max(int(max_menus), 1)
            note = "Cupo heredado del panel del BETA"
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "tenant_id": tenant_id,
                "user_id": user_id,
                "weeks": weeks,
                "granted_at": now,
                "expires_at": None,
                "source": "manual",
                "granted_by": None,
                "external_ref": None,
                "note": note,
            }
        )
    bind.execute(
        sa.text(
            "INSERT INTO membership_grants "
            "(id, tenant_id, user_id, weeks, granted_at, expires_at, source, "
            " granted_by, external_ref, note) "
            "VALUES (:id, :tenant_id, :user_id, :weeks, :granted_at, :expires_at, "
            " :source, :granted_by, :external_ref, :note)"
        ),
        rows,
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("max_menus", sa.Integer(), nullable=True))
    op.drop_index(op.f("ix_membership_grants_source"), table_name="membership_grants")
    op.drop_index(op.f("ix_membership_grants_user_id"), table_name="membership_grants")
    op.drop_index(
        op.f("ix_membership_grants_tenant_id"), table_name="membership_grants"
    )
    op.drop_table("membership_grants")
