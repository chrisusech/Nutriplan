"""Modelos SQLAlchemy (tablas de la sección 7.4).

Regla de oro: cada tabla con datos de cliente lleva tenant_id UUID NOT NULL
indexado. Los alimentos globales (USDA) viven en foods con tenant_id NULL;
los custom llevan su tenant_id.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nutriplan.adapters.db.session import Base


class TenantRow(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UserRow(Base):
    """La cuenta. Cada usuario final es dueño de su propio tenant."""

    __tablename__ = "users"
    __table_args__ = (
        # Un mismo `sub` de Google no puede abrir dos cuentas.
        UniqueConstraint("provider", "provider_subject", name="users_provider_identity"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    # Cómo entra. Con Google/Apple no hay hash: el proveedor guarda la credencial.
    provider: Mapped[str] = mapped_column(String(20), default="password")
    provider_subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Consentimiento explícito de analítica: sin fecha aquí no se registra nada
    # suyo en app_events. Requisito de tienda y de decencia.
    consent_analytics_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Override manual del super_user. NULL = se aplica la regla del BETA.
    max_menus: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Borrado de cuenta (Apple 5.1.1(v)): se marca y se purga, no se deja huérfano.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientRow(Base):
    __tablename__ = "clients"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("tenants.id"), index=True)
    # El perfil de UNA cuenta. El nombre no se duplica aquí: vive en users.name.
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), unique=True)
    sex: Mapped[str] = mapped_column(String(10))
    age_years: Mapped[int] = mapped_column(Integer)
    height_cm: Mapped[float] = mapped_column(Float)
    weight_kg: Mapped[float] = mapped_column(Float)
    goal: Mapped[str] = mapped_column(String(20))
    activity_level: Mapped[str] = mapped_column(String(20))
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    restrictions: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Lo que no quiere ver aunque no sea alergia; entra al filtro del catálogo.
    dislikes: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    # "le encanta cocinar", "come afuera", "entrena de noche", "come rápido".
    context_tags: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    eating_pattern_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Las comidas que hace al día. NULL = las cinco de siempre (clientes de antes
    # de que esto se pudiera elegir).
    meal_slots: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # LA comida libre de la semana: qué día (0-6) y cuál. NULL = no tiene.
    free_meal_day: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    free_meal_slot: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # CUAL de sus planes es EL plan. Un puntero, no una bandera por fila: "uno solo
    # activo" queda garantizado por la cardinalidad de la columna y no por codigo.
    # Archivar es repuntar; volver a la v2, tambien. Sin FK a proposito: seria un
    # ciclo clients <-> plan_cycles y las dos tablas se bloquearian al crear.
    active_plan_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)

    # El nombre se lee de aquí: `Client.name` no tiene columna propia.
    account: Mapped["UserRow"] = relationship(lazy="joined")
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


class ClientFoodBanRow(Base):
    """Alimentos vetados para un cliente (Fase 6 — edición quirúrgica)."""

    __tablename__ = "client_food_bans"
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
    fiber_100g: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Términos del intake que resuelven a este alimento ("pollo" → pechuga de pollo).
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    default_unit_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_granularity: Mapped[str] = mapped_column(String(10), default="grams")
    unit_name: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # A qué múltiplo redondea el solver, y el piso propio del alimento.
    portion_step_g: Mapped[float] = mapped_column(Float, default=10.0, server_default="10")
    portion_min_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    portion_max_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Afinidad por comida: antes era una lista de nombres hardcodeada en Python.
    meal_slots: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    # Y CUÁNTO encaja en cada una ({"almuerzo": 3}). Vacío = todo al peso por
    # defecto, que es como se comportaba el catálogo entero antes de existir esto.
    slot_weights: Mapped[dict[str, int]] = mapped_column(
        JSON, default=dict, server_default="{}"
    )
    # Alimentos libres (ensalada, café, gelatina): el solver los ignora.
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    free_text: Mapped[str | None] = mapped_column(String(60), nullable=True)


class NutritionTargetsRow(Base):
    __tablename__ = "nutrition_targets"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("clients.id"), index=True)
    daily: Mapped[dict[str, float]] = mapped_column(JSON)
    per_meal: Mapped[dict[str, Any]] = mapped_column(JSON)
    config_version: Mapped[str] = mapped_column(String(40))
    overrides: Mapped[dict[str, float]] = mapped_column(JSON, default=dict)
    formula: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # CON QUE PESO se calcularon estos macros. `clients.weight_kg` es mutable: al
    # registrar el peso del mes siguiente se perdia el del mes anterior, y con el la
    # unica forma de saber si el deficit estaba funcionando. La tabla ya era
    # append-only; el historial de macros existia, solo le faltaba el peso.
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PlanCycleRow(Base):
    __tablename__ = "plan_cycles"
    __table_args__ = (UniqueConstraint("tenant_id", "input_hash", "variant"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    client_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("clients.id"), index=True)
    targets_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("nutrition_targets.id"))
    variant: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # El numero humano del plan: "Plan nutricional v3". `variant` es su gemelo tecnico
    # (entra en el input_hash para que dos versiones no colisionen); esto es lo que el
    # entrenador lee.
    version: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(20), index=True)
    config_version: Mapped[str] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(60))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Rastro de la pasada crítica de IA (Fase 3): sin ella el plan sigue siendo válido.
    refined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    refine_model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    refine_prompt_version: Mapped[str | None] = mapped_column(String(60), nullable=True)

    days: Mapped[list["DayPlanRow"]] = relationship(
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="DayPlanRow.day_index",
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
    # Qué plato es, no solo qué alimentos lleva: "Tostada de huevos con aguacate".
    # `dish_key` es la clave de caché de su receta en dish_recipes.
    template_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    dish_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    dish_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    computed: Mapped[dict[str, float]] = mapped_column(JSON)
    free_salad: Mapped[bool] = mapped_column(Boolean, default=False)
    # LA comida libre: sin alimentos y sin macros. Sus calorías no se cuentan, y el
    # día suma por debajo del objetivo a propósito.
    is_free_meal: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    free_protein: Mapped[bool] = mapped_column(Boolean, default=False)

    items: Mapped[list["MealItemRow"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="MealItemRow.position"
    )


class MealItemRow(Base):
    __tablename__ = "meal_items"
    __table_args__ = (
        CheckConstraint(
            "(food_id IS NOT NULL AND recipe_id IS NULL) OR "
            "(food_id IS NULL AND recipe_id IS NOT NULL)",
            name="meal_items_one_source",
        ),
        CheckConstraint(
            "is_free OR (grams IS NOT NULL AND grams > 0)",
            name="meal_items_grams_positive",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    meal_entry_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("meal_entries.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    food_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("foods.id"), nullable=True)
    recipe_id: Mapped[UUID | None] = mapped_column(Uuid, ForeignKey("recipes.id"), nullable=True)
    grams: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_free: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class GenerationJobRow(Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RecipeRow(Base):
    __tablename__ = "recipes"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    name: Mapped[str] = mapped_column(String(200))
    # Vacío = receta declarada POR MACROS (un plato de restaurante con sus números
    # exactos), no por ingredientes. Los macros son entonces el dato, no el derivado.
    ingredients: Mapped[list[dict[str, Any]]] = mapped_column(JSON)  # [{food_id, grams}]
    macros: Mapped[dict[str, float]] = mapped_column(JSON)
    total_grams: Mapped[float] = mapped_column(Float)
    # En qué comidas encaja el plato. Sin esto, el alimento compuesto que sale al
    # verificarla deriva sus comidas de la CATEGORÍA: una hamburguesa acababa siendo
    # apta para desayuno.
    meal_slots: Mapped[list[str]] = mapped_column(JSON, default=list, server_default="[]")
    status: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    created_by: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    compound_food_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)


class AuditLogRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[UUID] = mapped_column(Uuid)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DishRecipeRow(Base):
    """Cómo se prepara un plato concreto. Caché GLOBAL, no por tenant.

    La misma combinación de plantilla + alimentos + gramos da la misma receta para
    todo el mundo, así que se genera una vez y se reparte. Es lo que hace viable la
    cuota gratis del LLM: a partir de cierto uso, casi todo es acierto de caché.
    """

    __tablename__ = "dish_recipes"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # hash(template_id + food_ids ordenados + locale)
    dish_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    template_id: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    name_es: Mapped[str] = mapped_column(String(160))
    ingredients: Mapped[list[str]] = mapped_column(JSON, default=list)
    steps: Mapped[list[str]] = mapped_column(JSON, default=list)
    prep_minutes: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tips: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="ai")  # yaml | ai
    model: Mapped[str | None] = mapped_column(String(60), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DishRatingRow(Base):
    """Qué le pareció el plato. El dato que justifica el BETA."""

    __tablename__ = "dish_ratings"
    __table_args__ = (
        UniqueConstraint("plan_cycle_id", "day_index", "slot", name="dish_ratings_one_per_meal"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    plan_cycle_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("plan_cycles.id"), index=True)
    day_index: Mapped[int] = mapped_column(SmallInteger)
    slot: Mapped[str] = mapped_column(String(20))
    # Se copian del plato en vez de referenciarlo: el rating tiene que sobrevivir a
    # que el plan se borre, porque el agregado "qué platos gustan" es el producto.
    template_id: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
    dish_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    rating: Mapped[int] = mapped_column(SmallInteger)  # 1-5
    would_repeat: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AppFeedbackRow(Base):
    """Lo que la gente tiene para decir durante el BETA."""

    __tablename__ = "app_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID] = mapped_column(Uuid, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), index=True)
    category: Mapped[str] = mapped_column(String(20), index=True)  # bug|idea|receta|general
    message: Mapped[str] = mapped_column(Text)
    nps: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)  # web|ios|android
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class AppEventRow(Base):
    """El embudo, evento a evento. Sin PII: eso vive en las otras tablas.

    `tenant_id` y `user_id` son nullable a propósito — hay eventos antes de que
    exista la cuenta, y hay que poder anonimizarlos al borrarla.
    """

    __tablename__ = "app_events"
    __table_args__ = (Index("ix_app_events_name_at", "name", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    user_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(60))
    props: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(20), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
