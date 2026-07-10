"""Modelos SQLAlchemy (tablas de la sección 7.4).

Regla de oro: cada tabla con datos de cliente lleva tenant_id UUID NOT NULL
indexado. Los alimentos globales (USDA) viven en foods con tenant_id NULL;
los custom llevan su tenant_id.
"""

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nutriplan.adapters.db.session import Base


class TenantRow(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, unique=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="trainer")
    client_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)  # cuentas de cliente


class ClientRow(Base):
    __tablename__ = "clients"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    sex: Mapped[str] = mapped_column(String(10))
    birthdate: Mapped[date | None] = mapped_column(Date, nullable=True)
    age_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_cm: Mapped[float] = mapped_column(Float)
    weight_kg: Mapped[float] = mapped_column(Float)
    goal: Mapped[str] = mapped_column(String(20))
    activity_level: Mapped[str] = mapped_column(String(20))
    restrictions: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    preferences: Mapped[list["ClientFoodPreferenceRow"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class ClientFoodPreferenceRow(Base):
    __tablename__ = "client_food_preferences"
    __table_args__ = (UniqueConstraint("client_id", "food_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("clients.id"), index=True)
    food_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("foods.id"))


class FoodRow(Base):
    __tablename__ = "foods"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid, index=True, nullable=True)
    source: Mapped[str] = mapped_column(String(20))
    source_ref: Mapped[str | None] = mapped_column(String(50), nullable=True)
    name_es: Mapped[str] = mapped_column(String(200))
    name_norm: Mapped[str] = mapped_column(String(200), index=True)
    name_en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    category: Mapped[str] = mapped_column(String(20), index=True)
    kcal_100g: Mapped[float] = mapped_column(Float)
    protein_100g: Mapped[float] = mapped_column(Float)
    carb_100g: Mapped[float] = mapped_column(Float)
    fat_100g: Mapped[float] = mapped_column(Float)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    default_unit_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_granularity: Mapped[str] = mapped_column(String(10), default="grams")
    unit_name: Mapped[str | None] = mapped_column(String(30), nullable=True)


class IntakeDocumentRow(Base):
    __tablename__ = "intake_documents"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    client_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    source_filename: Mapped[str] = mapped_column(String(300))
    raw_text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ambiguities: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NutritionTargetsRow(Base):
    __tablename__ = "nutrition_targets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("clients.id"), index=True)
    daily: Mapped[dict[str, float]] = mapped_column(JSON)
    per_meal: Mapped[dict[str, Any]] = mapped_column(JSON)
    method: Mapped[str] = mapped_column(String(40), default="mifflin_st_jeor")
    config_version: Mapped[str] = mapped_column(String(40))
    overrides: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    formula: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanCycleRow(Base):
    __tablename__ = "plan_cycles"
    __table_args__ = (UniqueConstraint("tenant_id", "input_hash", "phase"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("clients.id"), index=True)
    targets_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("nutrition_targets.id"))
    phase: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    config_version: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    days: Mapped[list["DayPlanRow"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="DayPlanRow.day_index"
    )


class DayPlanRow(Base):
    __tablename__ = "day_plans"
    __table_args__ = (UniqueConstraint("plan_cycle_id", "day_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    plan_cycle_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("plan_cycles.id"), index=True)
    day_index: Mapped[int] = mapped_column(Integer)
    totals: Mapped[dict[str, float]] = mapped_column(JSON)

    meals: Mapped[list["MealEntryRow"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="MealEntryRow.position"
    )


class MealEntryRow(Base):
    __tablename__ = "meal_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    day_plan_id: Mapped[int] = mapped_column(Integer, ForeignKey("day_plans.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)  # orden dentro del día
    slot: Mapped[str] = mapped_column(String(20))
    portions: Mapped[list[dict[str, Any]]] = mapped_column(JSON)  # [{food_id, grams}]
    computed: Mapped[dict[str, float]] = mapped_column(JSON)
    free_salad: Mapped[bool] = mapped_column(Boolean, default=False)
    free_protein: Mapped[bool] = mapped_column(Boolean, default=False)


class GenerationJobRow(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ExportArtifactRow(Base):
    __tablename__ = "export_artifacts"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    plan_cycle_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("plan_cycles.id"), index=True)
    format: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[UUID] = mapped_column(Uuid)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
