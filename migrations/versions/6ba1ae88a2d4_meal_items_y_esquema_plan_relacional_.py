"""meal_items y esquema plan relacional fase 4

Revision ID: 6ba1ae88a2d4
Revises: 110fb055d3be
Create Date: 2026-07-13 12:21:29.761182

"""
from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6ba1ae88a2d4"
down_revision: Union[str, Sequence[str], None] = "110fb055d3be"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _backfill_meal_items(connection: sa.Connection) -> None:
    """Copia meal_entries.portions (JSON legacy) a filas meal_items con FK."""
    rows = connection.execute(
        sa.text("SELECT id, tenant_id, portions FROM meal_entries")
    ).mappings()
    for entry in rows:
        portions: Any = entry["portions"]
        if not portions:
            continue
        if isinstance(portions, str):
            portions = json.loads(portions)
        for i, portion in enumerate(portions):
            food_id = portion.get("food_id")
            grams = portion.get("grams")
            if not food_id or not grams:
                continue
            connection.execute(
                sa.text(
                    "INSERT INTO meal_items "
                    "(tenant_id, meal_entry_id, position, food_id, grams, is_free, is_locked) "
                    "VALUES (:tenant_id, :meal_entry_id, :position, :food_id, :grams, 0, 0)"
                ),
                {
                    "tenant_id": entry["tenant_id"],
                    "meal_entry_id": entry["id"],
                    "position": i,
                    "food_id": food_id,
                    "grams": float(grams),
                },
            )


def _upgrade_sqlite() -> None:
    """SQLite no nombra los UNIQUE inline; recreamos las tablas afectadas."""
    op.execute("PRAGMA foreign_keys=OFF")

    op.create_table(
        "meal_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("meal_entry_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("food_id", sa.Uuid(), nullable=True),
        sa.Column("recipe_id", sa.Uuid(), nullable=True),
        sa.Column("grams", sa.Float(), nullable=True),
        sa.Column("is_free", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_locked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "(food_id IS NOT NULL AND recipe_id IS NULL) OR "
            "(food_id IS NULL AND recipe_id IS NOT NULL)",
            name="meal_items_one_source",
        ),
        sa.CheckConstraint(
            "is_free OR (grams IS NOT NULL AND grams > 0)",
            name="meal_items_grams_positive",
        ),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"]),
        sa.ForeignKeyConstraint(["meal_entry_id"], ["meal_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_items_meal_entry_id", "meal_items", ["meal_entry_id"])
    op.create_index("ix_meal_items_tenant_id", "meal_items", ["tenant_id"])

    op.execute(
        """
        CREATE TABLE day_plans_new (
            id INTEGER NOT NULL PRIMARY KEY,
            tenant_id CHAR(32) NOT NULL,
            plan_cycle_id CHAR(32) NOT NULL,
            phase VARCHAR(20) NOT NULL DEFAULT 'first_15',
            day_index INTEGER NOT NULL,
            totals JSON NOT NULL,
            FOREIGN KEY(plan_cycle_id) REFERENCES plan_cycles (id),
            UNIQUE (plan_cycle_id, phase, day_index)
        )
        """
    )
    op.execute(
        """
        INSERT INTO day_plans_new (id, tenant_id, plan_cycle_id, phase, day_index, totals)
        SELECT id, tenant_id, plan_cycle_id, 'first_15', day_index, totals FROM day_plans
        """
    )
    op.execute("DROP TABLE day_plans")
    op.execute("ALTER TABLE day_plans_new RENAME TO day_plans")
    op.create_index("ix_day_plans_plan_cycle_id", "day_plans", ["plan_cycle_id"])
    op.create_index("ix_day_plans_tenant_id", "day_plans", ["tenant_id"])

    op.execute(
        """
        CREATE TABLE plan_cycles_new (
            id CHAR(32) NOT NULL PRIMARY KEY,
            tenant_id CHAR(32) NOT NULL,
            client_id CHAR(32) NOT NULL,
            targets_id CHAR(32) NOT NULL,
            duration_days SMALLINT NOT NULL DEFAULT 15,
            variant INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL,
            config_version VARCHAR(40) NOT NULL,
            prompt_version VARCHAR(60) NOT NULL,
            model VARCHAR(60) NOT NULL,
            input_hash VARCHAR(64) NOT NULL,
            created_by CHAR(32),
            created_at DATETIME NOT NULL,
            approved_at DATETIME,
            edited_at DATETIME,
            edited_by CHAR(32),
            edit_count INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(client_id) REFERENCES clients (id),
            FOREIGN KEY(targets_id) REFERENCES nutrition_targets (id),
            UNIQUE (tenant_id, input_hash, variant)
        )
        """
    )
    op.execute(
        """
        INSERT INTO plan_cycles_new (
            id, tenant_id, client_id, targets_id, duration_days, variant, status,
            config_version, prompt_version, model, input_hash, created_by, created_at,
            approved_at, edited_at, edited_by, edit_count
        )
        SELECT
            id, tenant_id, client_id, targets_id, 15, 0, status,
            config_version, prompt_version, model, input_hash, created_by, created_at,
            approved_at, NULL, NULL, 0
        FROM plan_cycles
        """
    )
    op.execute("DROP TABLE plan_cycles")
    op.execute("ALTER TABLE plan_cycles_new RENAME TO plan_cycles")
    op.create_index("ix_plan_cycles_client_id", "plan_cycles", ["client_id"])
    op.create_index("ix_plan_cycles_input_hash", "plan_cycles", ["input_hash"])
    op.create_index("ix_plan_cycles_status", "plan_cycles", ["status"])
    op.create_index("ix_plan_cycles_tenant_id", "plan_cycles", ["tenant_id"])

    _backfill_meal_items(op.get_bind())
    op.execute("PRAGMA foreign_keys=ON")


def _upgrade_postgres() -> None:
    op.create_table(
        "meal_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("meal_entry_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("food_id", sa.Uuid(), nullable=True),
        sa.Column("recipe_id", sa.Uuid(), nullable=True),
        sa.Column("grams", sa.Float(), nullable=True),
        sa.Column("is_free", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_locked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("note", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "(food_id IS NOT NULL AND recipe_id IS NULL) OR "
            "(food_id IS NULL AND recipe_id IS NOT NULL)",
            name="meal_items_one_source",
        ),
        sa.CheckConstraint(
            "is_free OR (grams IS NOT NULL AND grams > 0)",
            name="meal_items_grams_positive",
        ),
        sa.ForeignKeyConstraint(["food_id"], ["foods.id"]),
        sa.ForeignKeyConstraint(["meal_entry_id"], ["meal_entries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipe_id"], ["recipes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_meal_items_meal_entry_id", "meal_items", ["meal_entry_id"])
    op.create_index("ix_meal_items_tenant_id", "meal_items", ["tenant_id"])

    op.add_column(
        "day_plans",
        sa.Column("phase", sa.String(length=20), server_default="first_15", nullable=False),
    )
    op.drop_constraint("day_plans_plan_cycle_id_day_index_key", "day_plans", type_="unique")
    op.create_unique_constraint(
        "uq_day_plans_plan_cycle_id_phase_day_index",
        "day_plans",
        ["plan_cycle_id", "phase", "day_index"],
    )

    op.add_column(
        "plan_cycles",
        sa.Column("duration_days", sa.SmallInteger(), server_default="15", nullable=False),
    )
    op.add_column("plan_cycles", sa.Column("variant", sa.Integer(), server_default="0", nullable=False))
    op.add_column("plan_cycles", sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("plan_cycles", sa.Column("edited_by", sa.Uuid(), nullable=True))
    op.add_column(
        "plan_cycles", sa.Column("edit_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.drop_constraint("plan_cycles_tenant_id_input_hash_phase_key", "plan_cycles", type_="unique")
    op.create_unique_constraint(
        "uq_plan_cycles_tenant_id_input_hash_variant",
        "plan_cycles",
        ["tenant_id", "input_hash", "variant"],
    )
    op.drop_column("plan_cycles", "phase")

    _backfill_meal_items(op.get_bind())


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        _upgrade_sqlite()
    else:
        _upgrade_postgres()


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        op.drop_index("ix_meal_items_tenant_id", table_name="meal_items")
        op.drop_index("ix_meal_items_meal_entry_id", table_name="meal_items")
        op.drop_table("meal_items")
        # downgrade parcial: no recreamos el esquema viejo en SQLite dev
        return

    op.drop_index("ix_meal_items_tenant_id", table_name="meal_items")
    op.drop_index("ix_meal_items_meal_entry_id", table_name="meal_items")
    op.drop_table("meal_items")

    op.drop_constraint("uq_day_plans_plan_cycle_id_phase_day_index", "day_plans", type_="unique")
    op.create_unique_constraint(
        "day_plans_plan_cycle_id_day_index_key", "day_plans", ["plan_cycle_id", "day_index"]
    )
    op.drop_column("day_plans", "phase")

    op.add_column(
        "plan_cycles",
        sa.Column("phase", sa.String(length=20), server_default="first_15", nullable=False),
    )
    op.drop_constraint("uq_plan_cycles_tenant_id_input_hash_variant", "plan_cycles", type_="unique")
    op.create_unique_constraint(
        "plan_cycles_tenant_id_input_hash_phase_key",
        "plan_cycles",
        ["tenant_id", "input_hash", "phase"],
    )
    op.drop_column("plan_cycles", "edit_count")
    op.drop_column("plan_cycles", "edited_by")
    op.drop_column("plan_cycles", "edited_at")
    op.drop_column("plan_cycles", "variant")
    op.drop_column("plan_cycles", "duration_days")
