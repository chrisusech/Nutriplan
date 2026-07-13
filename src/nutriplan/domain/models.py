"""Entidades y value objects del dominio (Pydantic v2).

Todos los IDs son UUID. Todos los agregados con datos de personas llevan
tenant_id. Ningún modelo de este módulo conoce I/O, frameworks ni la IA.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class UnitGranularity(StrEnum):
    """Cómo se porciona un alimento. Evita '5.5 huevos'."""

    GRAMS = "grams"   # arroz, pollo, yogur → gramos libres (múltiplos de 5 g)
    WHOLE = "whole"   # huevo, lata de atún → solo unidades enteras
    HALF = "half"     # aguacate, pan, banano → medias unidades permitidas


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


# Paso de redondeo por defecto para lo que se pesa a granel. 10 g y no 5 porque
# es más fácil pesar 120 o 150 g que 137: la báscula la usa una persona, no el
# solver.
BULK_PORTION_STEP_G = 10.0

# Con qué comidas encaja cada categoría cuando el catálogo no lo dice. Es solo
# el fallback (un alimento custom del entrenador, p.ej.): el CSV curado declara
# los slots uno a uno, porque el huevo va a desayuno y el salmón no.
_DEFAULT_SLOTS: dict[FoodCategory, tuple[MealSlot, ...]] = {
    FoodCategory.PROTEIN: (MealSlot.LUNCH, MealSlot.DINNER),
    FoodCategory.CARB: (MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER),
    FoodCategory.FAT: tuple(MealSlot),
    FoodCategory.FRUIT: (MealSlot.BREAKFAST, MealSlot.SNACK_AM, MealSlot.SNACK_PM),
    FoodCategory.VEGETABLE: (MealSlot.LUNCH, MealSlot.DINNER),
    FoodCategory.DAIRY: (MealSlot.BREAKFAST, MealSlot.SNACK_AM, MealSlot.SNACK_PM),
    FoodCategory.OTHER: tuple(MealSlot),
}


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
    fiber_100g: float = Field(default=0.0, ge=0)
    tags: list[str] = []  # "mariscos","gluten","lacteo","vegano",...

    # Cómo llama la gente a este alimento en el intake ("pollo" → pechuga de pollo).
    # Un alias GANA al fuzzy: sin él, "pollo" empata entre pechuga y muslo y el
    # ganador lo decidía el orden de iteración. El alias es la elección explícita
    # del entrenador sobre cuál es el alimento canónico de un término común.
    aliases: list[str] = []
    default_unit_g: float | None = None  # gramos de una unidad/porción típica (1 huevo≈50g)
    unit_granularity: UnitGranularity = UnitGranularity.GRAMS
    unit_name: str | None = None  # "huevo", "lata", "rebanada", "unidad" (para contar)

    # Cuánto salta la porción al redondear. Lo contable salta de unidad en unidad
    # (un huevo = 50 g); lo que se pesa, de 10 en 10 g.
    portion_step_g: float = Field(default=BULK_PORTION_STEP_G, gt=0)
    portion_min_g: float | None = Field(default=None, gt=0)  # None → el piso global
    portion_max_g: float | None = Field(default=None, gt=0)  # None → tope global del solver

    # En qué comidas encaja. Antes esto vivía hardcodeado por nombre en Python;
    # ahora es un dato del alimento y viaja desde la base.
    meal_slots: list[MealSlot] = []

    # Alimentos libres (ensalada, café, gelatina): no aportan macros y el solver
    # ni los mira. `free_text` es cómo se imprimen en el plan.
    is_free: bool = False
    free_text: str | None = None

    @model_validator(mode="after")
    def _fill_derived_defaults(self) -> "FoodItem":
        if not self.meal_slots:
            self.meal_slots = list(_DEFAULT_SLOTS[self.category])
        if "portion_step_g" not in self.model_fields_set:
            self.portion_step_g = _derive_portion_step(
                self.unit_granularity, self.default_unit_g
            )
        return self


def _derive_portion_step(granularity: UnitGranularity, unit_g: float | None) -> float:
    """Paso de porción de un alimento que no lo declara.

    Lo contable salta de unidad (o media unidad) para que el plan nunca pida
    '5.5 huevos'; el resto va en gramos redondos.
    """
    if unit_g and granularity is UnitGranularity.WHOLE:
        return unit_g
    if unit_g and granularity is UnitGranularity.HALF:
        return unit_g / 2.0
    return BULK_PORTION_STEP_G


class MacroTargets(BaseModel):
    kcal: float
    protein_g: float
    carb_g: float
    fat_g: float
    # La fibra es un piso, no una banda: pasarse no es un problema. Default 0
    # para que los planes ya persistidos (sin fibra) sigan deserializando.
    fiber_g: float = 0.0


class MacroFormula(BaseModel):
    """Cómo se fijan los macros diarios (el lever principal, no la IA).

    g/kg es el pensamiento del entrenador: proteína y grasa por kg de peso; el
    carbohidrato cierra el resto hasta las kcal objetivo. Cualquier campo en
    None usa el default de la config por objetivo — una fórmula vacía reproduce
    exactamente el cálculo base (los golden tests siguen valiendo).
    """

    protein_g_per_kg: float | None = Field(default=None, gt=0)
    fat_g_per_kg: float | None = Field(default=None, gt=0)
    kcal_override: float | None = Field(default=None, gt=0)  # None = TDEE del objetivo


class NutritionTargets(BaseModel):
    id: UUID
    tenant_id: UUID
    client_id: UUID
    daily: MacroTargets
    per_meal: dict[MealSlot, MacroTargets]
    method: str = "mifflin_st_jeor"
    config_version: str  # procedencia
    overrides: dict[str, float] = {}  # ajustes manuales del entrenador (gramos sueltos)
    formula: MacroFormula = Field(default_factory=MacroFormula)  # g/kg y kcal elegidos
    computed_at: datetime


class MealFoodPortion(BaseModel):
    food_id: UUID
    grams: float = Field(gt=0)


class PlanPhase(StrEnum):
    FIRST_15 = "first_15"
    NEXT_15 = "next_15"


class MealItem(BaseModel):
    """Un alimento (o receta) dentro de una comida — unidad editable con id estable.

    Los ids de fila viven en la base; en memoria pueden ser None hasta persistir.
    `is_locked` evita que un re-solve pise gramos ajustados a mano (Fase 6).
    """

    id: int | None = None
    food_id: UUID | None = None
    recipe_id: UUID | None = None
    grams: float | None = None
    is_free: bool = False
    is_locked: bool = False
    note: str | None = None
    position: int = 0

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "MealItem":
        has_food = self.food_id is not None
        has_recipe = self.recipe_id is not None
        if has_food == has_recipe:
            raise ValueError("MealItem requiere food_id o recipe_id, no ambos ni ninguno")
        if not self.is_free and (self.grams is None or self.grams <= 0):
            raise ValueError("MealItem con macros debe tener grams > 0")
        return self


class MealEntry(BaseModel):
    id: int | None = None  # fila meal_entries; estable para URLs HTMX
    slot: MealSlot
    items: list[MealItem] = []
    computed: MacroTargets  # recalculado por el código, nunca por la IA
    free_salad: bool = False
    free_protein: bool = False  # "proteína libre" del formato

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_portions(cls, data: Any) -> Any:
        """Tests y generate_plan siguen pasando `portions=` hasta migrar todo a items."""
        if not isinstance(data, dict) or data.get("items"):
            return data
        portions = data.pop("portions", None)
        if portions is None:
            return data
        items: list[dict[str, Any]] = []
        for i, p in enumerate(portions):
            if isinstance(p, dict):
                items.append({"food_id": p["food_id"], "grams": p["grams"], "position": i})
            else:
                items.append({"food_id": p.food_id, "grams": p.grams, "position": i})
        data["items"] = items
        return data

    @property
    def portions(self) -> list[MealFoodPortion]:
        """Vista de compatibilidad: porciones pesables con macros."""
        return [
            MealFoodPortion(food_id=item.food_id, grams=item.grams)
            for item in self.items
            if item.food_id is not None
            and item.grams is not None
            and item.grams > 0
            and not item.is_free
        ]

    @portions.setter
    def portions(self, value: list[MealFoodPortion]) -> None:
        """Traduce ediciones legacy (por food_id+grams) a items."""
        self.items = [
            MealItem(food_id=p.food_id, grams=p.grams, position=i)
            for i, p in enumerate(value)
        ]


class DayPlan(BaseModel):
    day_index: int = Field(ge=0, le=6)  # 0..6 (lunes..domingo)
    phase: PlanPhase = PlanPhase.FIRST_15
    meals: list[MealEntry]
    totals: MacroTargets


class PlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    EXPORTED = "exported"


class PlanCycle(BaseModel):
    id: UUID
    tenant_id: UUID
    client_id: UUID
    targets_id: UUID
    days: list[DayPlan]  # 7 días por bloque de fase
    duration_days: int = Field(default=15, ge=15, le=30)
    variant: int = Field(default=0, ge=0)
    status: PlanStatus = PlanStatus.DRAFT
    # Procedencia (reproducibilidad):
    config_version: str
    prompt_version: str
    model: str
    input_hash: str
    created_by: UUID | None = None
    created_at: datetime
    approved_at: datetime | None = None
    edited_at: datetime | None = None
    edited_by: UUID | None = None
    edit_count: int = 0

    @property
    def is_edited(self) -> bool:
        return self.edit_count > 0 or self.edited_at is not None


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
    parsed: dict[str, Any]  # datos extraídos por la IA
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
    # El coral de la app. Antes el default era un verde que contradecía a
    # `branding_store.DEFAULT_BRANDING`: cualquier tenant creado sin elegir color
    # se guardaba en verde, y el PDF salía de otro producto que la web.
    primary_color: str = "#F26D5B"
    accent_color: str = "#F0925E"
    handle: str | None = None  # @instagram u otro


class Trainer(BaseModel):
    """Cuenta de acceso. Un entrenador es dueño de su tenant; un cliente ve solo
    su propio plan (client_id); un admin verifica recetas de todos."""

    id: UUID
    tenant_id: UUID
    name: str
    email: str
    role: str = "trainer"  # "trainer" | "client" | "admin"
    client_id: UUID | None = None  # solo para cuentas de cliente


class RecipeStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    REJECTED = "rejected"


class RecipeIngredient(BaseModel):
    food_id: UUID
    grams: float = Field(gt=0)


class Recipe(BaseModel):
    """Receta = nombre + ingredientes con macros agregados por el dominio.

    El entrenador la sube (pending); un admin verifica los macros (verified).
    Una receta verificada se materializa como 'alimento compuesto' del tenant
    y queda disponible para armar planes (aporta sus macros a un slot).
    """

    id: UUID
    tenant_id: UUID
    name: str
    ingredients: list[RecipeIngredient]
    macros: MacroTargets  # totales de la receta, calculados por el dominio
    total_grams: float = Field(gt=0)
    status: RecipeStatus = RecipeStatus.PENDING
    created_by: UUID | None = None  # cuenta de entrenador que la subió
    created_at: datetime
    compound_food_id: UUID | None = None  # alimento generado al verificar
