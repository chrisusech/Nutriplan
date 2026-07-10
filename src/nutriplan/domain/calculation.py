"""Motor de cálculo determinista (Módulo 2, secciones 9.1–9.4).

Código puro, sin IA, reproducible bit a bit. Fórmulas:
  BMR (Mifflin-St Jeor), TDEE = BMR * factor, kcal = TDEE * (1 + delta),
  proteína = g/kg * peso, grasa = kcal * pct / 9, carbo = resto / 4.
"""

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from nutriplan.domain.errors import CalculationError
from nutriplan.domain.models import (
    Client,
    MacroTargets,
    MealSlot,
    NutritionTargets,
    Sex,
)
from nutriplan.domain.nutrition_config import NutritionConfig

KCAL_PER_G_PROTEIN = 4.0
KCAL_PER_G_CARB = 4.0
KCAL_PER_G_FAT = 9.0

# Campos de MacroTargets que el entrenador puede fijar manualmente.
OVERRIDABLE_FIELDS = ("kcal", "protein_g", "carb_g", "fat_g")


def resolve_age_years(client: Client, *, today: date | None = None) -> int:
    """Edad desde birthdate (preferida) o age_years; sin ambas → error claro."""
    if client.birthdate is not None:
        ref = today or date.today()
        years = ref.year - client.birthdate.year
        if (ref.month, ref.day) < (client.birthdate.month, client.birthdate.day):
            years -= 1
        return years
    if client.age_years is not None:
        return client.age_years
    raise CalculationError(f"Cliente {client.id}: falta fecha de nacimiento o edad")


def bmr_mifflin_st_jeor(sex: Sex, weight_kg: float, height_cm: float, age_years: int) -> float:
    base = 10.0 * weight_kg + 6.25 * height_cm - 5.0 * age_years
    return base + 5.0 if sex == Sex.MALE else base - 161.0


def compute_daily_macros(client: Client, config: NutritionConfig) -> MacroTargets:
    age = resolve_age_years(client)
    bmr = bmr_mifflin_st_jeor(client.sex, client.weight_kg, client.height_cm, age)
    tdee = bmr * config.activity_factors[client.activity_level]
    kcal = tdee * (1.0 + config.goal_adjustments[client.goal])

    protein_g = config.protein_g_per_kg[client.goal] * client.weight_kg
    fat_g = (kcal * config.fat_pct_of_kcal) / KCAL_PER_G_FAT
    carb_g = (kcal - protein_g * KCAL_PER_G_PROTEIN - fat_g * KCAL_PER_G_FAT) / KCAL_PER_G_CARB

    if carb_g < 0:
        raise CalculationError(
            f"Cliente {client.id}: los macros no cierran (carbohidratos negativos: "
            f"{carb_g:.1f} g). Revisar peso/objetivo o fijar overrides."
        )

    return MacroTargets(
        kcal=round(kcal, 1),
        protein_g=round(protein_g, 1),
        carb_g=round(carb_g, 1),
        fat_g=round(fat_g, 1),
    )


def apply_overrides(daily: MacroTargets, overrides: dict[str, float]) -> MacroTargets:
    """Aplica overrides manuales del entrenador campo a campo.

    Si se fijan los tres macros pero no las kcal, las kcal se recomputan para
    mantener el invariante energético.
    """
    unknown = set(overrides) - set(OVERRIDABLE_FIELDS)
    if unknown:
        raise CalculationError(f"Overrides desconocidos: {sorted(unknown)}")

    values = daily.model_dump()
    values.update({k: float(v) for k, v in overrides.items()})

    macros_overridden = {"protein_g", "carb_g", "fat_g"} & set(overrides)
    if macros_overridden and "kcal" not in overrides:
        values["kcal"] = round(
            values["protein_g"] * KCAL_PER_G_PROTEIN
            + values["carb_g"] * KCAL_PER_G_CARB
            + values["fat_g"] * KCAL_PER_G_FAT,
            1,
        )
    return MacroTargets(**values)


def split_per_meal(
    daily: MacroTargets, distribution: dict[MealSlot, float]
) -> dict[MealSlot, MacroTargets]:
    """Reparto por comida: cada macro se multiplica por el % del slot."""
    return {
        slot: MacroTargets(
            kcal=round(daily.kcal * pct, 1),
            protein_g=round(daily.protein_g * pct, 1),
            carb_g=round(daily.carb_g * pct, 1),
            fat_g=round(daily.fat_g * pct, 1),
        )
        for slot, pct in distribution.items()
    }


def compute_targets(
    client: Client,
    config: NutritionConfig,
    *,
    overrides: dict[str, float] | None = None,
    targets_id: UUID | None = None,
    now: datetime | None = None,
) -> NutritionTargets:
    """De Client + config (+ overrides) → NutritionTargets con procedencia."""
    daily = compute_daily_macros(client, config)
    if overrides:
        daily = apply_overrides(daily, overrides)
    per_meal = split_per_meal(daily, config.meal_distribution)

    return NutritionTargets(
        id=targets_id or uuid4(),
        tenant_id=client.tenant_id,
        client_id=client.id,
        daily=daily,
        per_meal=per_meal,
        config_version=config.version,
        overrides=overrides or {},
        computed_at=now or datetime.now(UTC),
    )
