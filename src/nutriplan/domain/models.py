"""Entidades y value objects del dominio (Pydantic v2).

Todos los IDs son UUID. Todos los agregados con datos de personas llevan
tenant_id. Ningún modelo de este módulo conoce I/O, frameworks ni la IA.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nutriplan.domain.week import iso_week_start


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

    GRAMS = "grams"  # arroz, pollo, yogur → gramos libres (múltiplos de 5 g)
    WHOLE = "whole"  # huevo, lata de atún → solo unidades enteras
    HALF = "half"  # aguacate, pan, banano → medias unidades permitidas


# Las comidas "de plato" frente a los snacks. Ya no son obligatorias: basta
# con una comida al día. El presentador las usa para etiquetar, no para forzar.
CORE_MEAL_SLOTS = (MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER)

# El menú es de una semana. No es una configuración: es el producto.
DAYS_PER_WEEK = 7


class WeightEntry(BaseModel):
    """El cierre de una semana: el peso y lo que la persona tuvo que decir.

    Uno por semana ISO (lunes); el historial no se borra. Es la fila que cierra
    la semana N y abre la N+1.
    """

    id: UUID
    tenant_id: UUID
    client_id: UUID
    weight_kg: float = Field(gt=0, le=400)
    week_start: date  # lunes de esa semana
    logged_at: datetime
    # Nota generada por la IA tras adaptar; opcional y soft-fail.
    note: str | None = Field(default=None, max_length=400)
    # Lo que escribió la PERSONA sobre su semana. No confundir con `note`: esto
    # es materia prima del plan siguiente, aquello es narración de lo ya hecho.
    client_comment: str | None = Field(default=None, max_length=1200)


class Client(BaseModel):
    """El perfil nutricional de una persona.

    `name` no tiene columna propia: es el nombre de la cuenta (`users.name`), que
    el repositorio trae por `user_id`. La identidad vive en un solo sitio.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    name: str
    sex: Sex
    age_years: int = Field(ge=13, le=100)
    height_cm: float = Field(gt=0)
    weight_kg: float = Field(gt=0)
    goal: Goal
    activity_level: ActivityLevel
    # Dónde vive: lo que hace que el plan sepa a comida de su región.
    city: str | None = None
    country: str | None = None
    liked_food_ids: list[UUID] = []  # vacío = "sorpréndeme", todo el catálogo
    restrictions: list[str] = []  # tags: "no_seafood","no_gluten","no_shake",...
    # Lo que NO quiere ver aunque no sea alergia. Antes se perdía en `notes` y
    # nadie lo leía; ahora entra al filtro del catálogo.
    dislikes: list[str] = []
    # Cómo es su vida alrededor de la comida: "le encanta cocinar", "come afuera",
    # "entrena de noche", "come rápido".
    context_tags: list[str] = []
    # Su relato libre: "cómo es un día típico tuyo". Contexto para el selector y
    # para el crítico; no altera macros.
    eating_pattern_raw: str | None = None

    # Cuántas comidas hace al día, y CUÁLES. Cinco por defecto, que es lo normal;
    # pero quien come cuatro no tiene por qué recibir un plan de cinco. Vacío =
    # las cinco (planes ya guardados siguen deserializando).
    meal_slots: list[MealSlot] = []

    # Cuál de sus planes es EL plan: el definitivo, el que el cliente sigue. Un
    # puntero y no una bandera por plan, porque "uno solo activo" queda garantizado
    # por la cardinalidad de la columna y no por código que haya que recordar.
    active_plan_id: UUID | None = None

    # La comida libre de la semana: qué día (0-6) y cuál. Una, o ninguna.
    #
    # Vive en el CLIENTE y no en el plan porque se elige ANTES de generar, tiene que
    # sobrevivir a los re-render del generador y tiene que entrar en el hash de
    # entrada (si no, activarla devolvería el plan cacheado). Lo que queda dentro del
    # plan es el reflejo: `MealEntry.is_free_meal`.
    free_meal_day: int | None = Field(default=None, ge=0, le=6)
    free_meal_slot: MealSlot | None = None

    @model_validator(mode="after")
    def _default_meal_slots(self) -> "Client":
        chosen = set(self.meal_slots) or set(MealSlot)
        if not chosen:
            raise ValueError("Un plan necesita al menos una comida")
        self.meal_slots = [s for s in MealSlot if s in chosen]  # orden del día
        return self

    @model_validator(mode="after")
    def _coherent_free_meal(self) -> "Client":
        """O día y comida, o ninguno de los dos. Y ha de ser una comida que hace.

        El entrenador puede apagar el snack de la tarde DESPUÉS de haber puesto ahí
        la comida libre. Se limpia en vez de romper: una comida libre en una comida
        que no existe no significa nada.
        """
        if self.free_meal_slot is not None and self.free_meal_slot not in self.meal_slots:
            self.free_meal_day = None
            self.free_meal_slot = None
        elif (self.free_meal_day is None) != (self.free_meal_slot is None):
            self.free_meal_day = None
            self.free_meal_slot = None
        return self

    @property
    def free_meal(self) -> tuple[int, MealSlot] | None:
        """La celda libre de la semana, si la hay."""
        if self.free_meal_day is None or self.free_meal_slot is None:
            return None
        return (self.free_meal_day, self.free_meal_slot)


# Paso de redondeo por defecto para lo que se pesa a granel. 10 g y no 5 porque
# es más fácil pesar 120 o 150 g que 137: la báscula la usa una persona, no el
# solver.
BULK_PORTION_STEP_G = 10.0

# Cuánto ENCAJA un alimento en una comida. La afinidad era binaria —va o no va— y
# eso no describe una cocina: el arroz y la arepa "van" los dos en un almuerzo, y
# el motor los tomaba por equivalentes. La arepa es de desayuno; el arroz es de
# almuerzo. Sin un orden, salía pan en la cena y arepa a mediodía.
#   3 = es SU comida · 2 = va bien · 1 = va, pero no es su sitio
# El peso ORDENA, no prohíbe: quien solo tiene arepa sigue comiendo arepa al
# mediodía (lo contrario sería no poder generarle plan). Prohibir es cosa de
# `meal_slots`.
DEFAULT_SLOT_WEIGHT = 2
MAX_SLOT_WEIGHT = 3

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

    # CUÁNTO encaja en cada una (ver DEFAULT_SLOT_WEIGHT). Ausente = 2: los
    # alimentos que no lo declaran se comportan exactamente como antes.
    slot_weights: dict[MealSlot, int] = {}

    # Alimentos libres (ensalada, café, gelatina): no aportan macros y el solver
    # ni los mira. `free_text` es cómo se imprimen en el plan.
    is_free: bool = False
    free_text: str | None = None

    @model_validator(mode="after")
    def _fill_derived_defaults(self) -> "FoodItem":
        if not self.meal_slots:
            self.meal_slots = list(_DEFAULT_SLOTS[self.category])
        if "portion_step_g" not in self.model_fields_set:
            self.portion_step_g = _derive_portion_step(self.unit_granularity, self.default_unit_g)
        return self

    def weight_in(self, slot: MealSlot) -> int:
        """Cuánto encaja este alimento en esta comida. 0 si no va."""
        if slot not in self.meal_slots:
            return 0
        return self.slot_weights.get(slot, DEFAULT_SLOT_WEIGHT)


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
    # Ajustes manuales (gramos sueltos) y, además, las marcas de `OVERRIDE_FLAGS`.
    overrides: dict[str, float] = {}
    formula: MacroFormula = Field(default_factory=MacroFormula)  # g/kg y kcal elegidos
    # CON QUÉ PESO se calcularon estos macros. `Client.weight_kg` es mutable: al
    # registrar el peso del mes siguiente se perdía el del mes anterior, y con él la
    # única forma de saber si el déficit estaba funcionando. `| None` para que los
    # objetivos ya guardados sigan deserializando.
    weight_kg: float | None = Field(default=None, gt=0)
    computed_at: datetime


class MealFoodPortion(BaseModel):
    food_id: UUID
    grams: float = Field(gt=0)


class MealItem(BaseModel):
    """Un alimento (o receta) dentro de una comida — unidad editable con id estable.

    Los ids de fila viven en la base; en memoria pueden ser None hasta persistir.
    `is_locked` evita que un re-solve pise gramos ajustados a mano (Fase 6).
    """

    id: int | None = None
    food_id: UUID | None = None
    grams: float | None = None
    is_free: bool = False
    is_locked: bool = False
    position: int = 0

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "MealItem":
        if self.food_id is None:
            raise ValueError("MealItem requiere un food_id")
        if not self.is_free and (self.grams is None or self.grams <= 0):
            raise ValueError("MealItem con macros debe tener grams > 0")
        return self


class MealEntry(BaseModel):
    id: int | None = None  # fila meal_entries; estable para URLs HTMX
    slot: MealSlot
    # Qué plato es, no solo qué alimentos lleva. Lo pone el motor de platos y lo
    # puede renombrar el crítico: "Tostada de huevos con aguacate" en vez de una
    # lista. `dish_key` es la clave de caché de su receta.
    template_id: str | None = None
    dish_name: str | None = None
    dish_key: str | None = None
    items: list[MealItem] = []
    computed: MacroTargets  # recalculado por el código, nunca por la IA
    free_salad: bool = False
    free_protein: bool = False  # "proteína libre" del formato

    # LA comida libre de la semana: sin alimentos, sin gramos y sin macros. Sus
    # calorías no se cuentan, y las demás comidas de ese día conservan su objetivo
    # de siempre — el día suma por debajo a propósito, que es lo que significa
    # comerse una pizza el domingo.
    #
    # Es el reflejo, dentro del plan, de `Client.free_meal`. Está aquí y no en el
    # PlanCycle porque un plan ya exportado tiene que seguir diciendo lo que decía
    # aunque el cliente cambie de preferencia mañana.
    is_free_meal: bool = False
    # Lo marcó como comido hoy. El anillo cuenta estas kcal; el solver no
    # reporciona un plato que ya se comió.
    eaten: bool = False

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
            MealItem(food_id=p.food_id, grams=p.grams, position=i) for i, p in enumerate(value)
        ]


class DayPlan(BaseModel):
    day_index: int = Field(ge=0, le=6)  # 0..6 (lunes..domingo)
    meals: list[MealEntry]
    totals: MacroTargets


class PlanStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"


class PlanCycle(BaseModel):
    id: UUID
    tenant_id: UUID
    client_id: UUID
    targets_id: UUID
    days: list[DayPlan]  # exactamente 7, uno por día de la semana
    # A QUÉ semana pertenece el plan (lunes ISO). Sin esto, `day_index` es un
    # rótulo suelto: no hay forma de saber si un plan es el de esta semana o el
    # de hace un mes, y ni el historial ni el consumo de la membresía existen.
    week_start: date = Field(default_factory=iso_week_start)
    variant: int = Field(default=0, ge=0)
    # El número humano del plan ("Plan nutricional v3"). `variant` es su gemelo
    # técnico: entra en el input_hash para que dos versiones no colisionen en la
    # caché. Éste es el que el entrenador y el cliente leen.
    version: int = Field(default=1, ge=1)
    # OJO: `status` y "cuál es EL plan" son ejes distintos. `status` dice en qué punto
    # del flujo está ESTE plan (borrador/aprobado); cuál de todos es el definitivo lo
    # dice `Client.active_plan_id`. Un v3 recién generado puede ser borrador y ya ser
    # el activo.
    status: PlanStatus = PlanStatus.DRAFT
    # Procedencia (reproducibilidad):
    config_version: str
    prompt_version: str
    model: str
    input_hash: str
    created_at: datetime
    approved_at: datetime | None = None
    edited_at: datetime | None = None
    edit_count: int = 0
    # Rastro de la pasada crítica. Sin ella el menú es válido igual, solo que
    # sin nombres de plato y sin las sustituciones de sabor.
    refined_at: datetime | None = None
    refine_model: str | None = None
    refine_prompt_version: str | None = None


class MealSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: MealSlot
    food_ids: list[str]  # enum restringido en runtime al conjunto permitido
    free_salad: bool = False
    # Nombre culinario que propone la IA (o vacío en el motor). El solver no lo
    # mira; alimenta la UI y las recetas.
    dish_name: str | None = Field(default=None, max_length=80)


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


class Role(StrEnum):
    """Rol de la cuenta. `user` es quien usa la app; `super_user` es el dueño de
    la plataforma: ve las métricas globales y no tiene cuota."""

    USER = "user"
    SUPER_USER = "super_user"


class AuthProvider(StrEnum):
    PASSWORD = "password"
    GOOGLE = "google"
    APPLE = "apple"


class Account(BaseModel):
    """La cuenta de una persona. Cada `user` es dueño de su propio tenant.

    Existe desde el registro; su perfil nutricional (`Client`) solo aparece al
    terminar el onboarding, así que el nombre vive aquí y no allá.

    Cuántas semanas puede generar NO se guarda aquí: es el saldo de sus
    concesiones (`domain/membership.py`), que se calcula, no se copia.
    """

    id: UUID
    tenant_id: UUID
    name: str
    email: str
    role: Role = Role.USER
    provider: AuthProvider = AuthProvider.PASSWORD
    is_active: bool = True
    email_verified_at: datetime | None = None
    consent_analytics_at: datetime | None = None
