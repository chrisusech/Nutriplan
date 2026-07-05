"""Entidades y value objects del dominio (Pydantic v2).

Todos los IDs son UUID. Todos los agregados con datos de personas llevan
tenant_id. Ningún modelo de este módulo conoce I/O, frameworks ni la IA.
"""

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Sex(StrEnum):
    FEMALE = "female"
    MALE = "male"


class Goal(StrEnum):
    LOSE_FAT = "lose_fat"
    MAINTAIN = "maintain"
    GAIN_MUSCLE = "gain_muscle"


class ActivityLevel(StrEnum):
    SEDENTARY = "sedentary"
    LIGHT = "light"
    MODERATE = "moderate"
    ACTIVE = "active"
    VERY_ACTIVE = "very_active"


class MealSlot(StrEnum):
    BREAKFAST = "desayuno"
    SNACK_AM = "snack_am"
    LUNCH = "almuerzo"
    SNACK_PM = "snack_pm"
    DINNER = "cena"


class FoodCategory(StrEnum):
    PROTEIN = "protein"
    CARB = "carb"
    FAT = "fat"
    FRUIT = "fruit"
    VEGETABLE = "vegetable"
    DAIRY = "dairy"
    OTHER = "other"


class Client(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    sex: Sex
    birthdate: date | None = None  # preferible a edad fija
    age_years: int | None = None  # fallback si no hay fecha
    height_cm: float = Field(gt=0)
    weight_kg: float = Field(gt=0)
    goal: Goal
    activity_level: ActivityLevel
    liked_food_ids: list[UUID] = []  # alimentos que le gustan
    restrictions: list[str] = []  # tags: "no_seafood","no_gluten","no_shake",...
    notes: str | None = None


class FoodItem(BaseModel):
    id: UUID
    tenant_id: UUID | None = None  # None = alimento global; con id = custom del tenant
    source: str  # "USDA" | "TCAC" | "custom"
    source_ref: str | None = None  # p.ej. fdc_id de USDA
    name_es: str
    name_en: str | None = None
    category: FoodCategory
    kcal_100g: float = Field(ge=0)
    protein_100g: float = Field(ge=0)
    carb_100g: float = Field(ge=0)
    fat_100g: float = Field(ge=0)
    tags: list[str] = []  # "mariscos","gluten","lacteo","vegano",...
    default_unit_g: float | None = None  # gramos de una porción típica (1 huevo≈50g)


class MacroTargets(BaseModel):
    kcal: float
    protein_g: float
    carb_g: float
    fat_g: float


class NutritionTargets(BaseModel):
    id: UUID
    tenant_id: UUID
    client_id: UUID
    daily: MacroTargets
    per_meal: dict[MealSlot, MacroTargets]
    method: str = "mifflin_st_jeor"
    config_version: str  # procedencia
    overrides: dict = {}  # ajustes manuales del entrenador
    computed_at: datetime


class MealFoodPortion(BaseModel):
    food_id: UUID
    grams: float = Field(gt=0)


class MealEntry(BaseModel):
    slot: MealSlot
    portions: list[MealFoodPortion]
    computed: MacroTargets  # recalculado por el código, nunca por la IA
    free_salad: bool = False
    free_protein: bool = False  # "proteína libre" del formato


class DayPlan(BaseModel):
    day_index: int = Field(ge=0, le=6)  # 0..6 (lunes..domingo)
    meals: list[MealEntry]
    totals: MacroTargets


class PlanPhase(StrEnum):
    FIRST_15 = "first_15"
    NEXT_15 = "next_15"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXPORTED = "exported"


class PlanCycle(BaseModel):
    id: UUID
    tenant_id: UUID
    client_id: UUID
    targets_id: UUID
    phase: PlanPhase
    days: list[DayPlan]  # 7 días
    status: PlanStatus = PlanStatus.DRAFT
    # Procedencia (reproducibilidad):
    config_version: str
    prompt_version: str
    model: str
    input_hash: str
    created_by: UUID | None = None
    created_at: datetime
    approved_at: datetime | None = None


class IntakeStatus(StrEnum):
    PARSED = "parsed"
    NEEDS_REVIEW = "needs_review"
    CONFIRMED = "confirmed"


class IntakeDocument(BaseModel):
    id: UUID
    tenant_id: UUID
    client_id: UUID | None = None
    source_filename: str
    raw_text: str
    parsed: dict  # datos extraídos por la IA
    ambiguities: list[str] = []  # campos dudosos para revisión humana
    status: IntakeStatus
    created_at: datetime


# --- Contratos de la IA (Structured Outputs) ---


class LikedFoods(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proteins: list[str] = []
    carbs: list[str] = []
    fats: list[str] = []
    fruits: list[str] = []
    vegetables: list[str] = []


class IntakeParsed(BaseModel):
    model_config = ConfigDict(extra="forbid")  # requerido por Structured Outputs

    name: str
    sex: Sex
    age_years: int | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    weight_is_approximate: bool = False  # bandera de ambigüedad
    city: str | None = None
    goal_raw: str  # texto libre del objetivo
    liked_foods: LikedFoods  # listas por categoría (strings)
    restrictions_raw: list[str] = []  # "sin mariscos", etc.
    uses_protein_shake: bool | None = None
    training_raw: str | None = None


class MealSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: MealSlot
    food_ids: list[str]  # enum restringido en runtime al conjunto permitido
    free_salad: bool = False


class DaySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_index: int
    meals: list[MealSelection]


class PlanSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[DaySelection]  # 7 días


class Branding(BaseModel):
    tenant_name: str
    logo_path: str | None = None
    primary_color: str = "#2E7D32"
    accent_color: str = "#F9A825"
    handle: str | None = None  # @instagram u otro
