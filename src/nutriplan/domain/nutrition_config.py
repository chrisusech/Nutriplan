"""Schema de la estrategia nutricional (contrato del YAML versionado).

Vive en el dominio porque el motor de cálculo depende de su forma; el YAML en
sí se carga en un adaptador detrás del puerto ConfigProvider.
"""

from typing import Self

from pydantic import BaseModel, Field, model_validator

from nutriplan.domain.models import ActivityLevel, Goal, MealSlot, Sex


class Tolerances(BaseModel):
    kcal: float = Field(gt=0)
    protein_g: float = Field(gt=0)
    carb_g: float = Field(gt=0)
    fat_g: float = Field(gt=0)


class PortioningConfig(BaseModel):
    grams_rounding: int = Field(gt=0)
    min_portion_g: int = Field(gt=0)


class GenerationConfig(BaseModel):
    max_retries: int = Field(default=3, ge=1)
    max_protein_repeats_per_week: int = Field(default=3, ge=1)
    max_carb_repeats_per_week: int = Field(default=4, ge=1)


class FiberConfig(BaseModel):
    g_per_1000_kcal: float = Field(default=14.0, gt=0)
    min_fruit_servings: int = Field(default=2, ge=0)


# Los rangos de la estrategia: fuera de aquí, la config no carga. Es lo que
# impide que un typo en el YAML meta a alguien en 3 g/kg de proteína.
PROTEIN_G_PER_KG_RANGE = (1.6, 2.2)
FAT_G_PER_KG_RANGE = (0.8, 1.0)


class NutritionConfig(BaseModel):
    version: str
    activity_factors: dict[ActivityLevel, float]
    goal_adjustments: dict[Goal, float]
    protein_g_per_kg: dict[Goal, float]
    fat_g_per_kg: dict[Goal, float]
    fat_pct_of_kcal: float = Field(gt=0, lt=1)  # fallback si un goal no trae g/kg
    kcal_floor: dict[Sex, float]
    carb_floor_g_per_kg: float = Field(default=0.5, ge=0)
    fiber: FiberConfig = FiberConfig()
    meal_distribution: dict[MealSlot, float]
    tolerances: Tolerances
    portioning: PortioningConfig
    generation: GenerationConfig = GenerationConfig()

    @model_validator(mode="after")
    def _validate_completeness(self) -> Self:
        if set(self.activity_factors) != set(ActivityLevel):
            raise ValueError("activity_factors debe cubrir todos los niveles de actividad")
        if set(self.goal_adjustments) != set(Goal) or set(self.protein_g_per_kg) != set(Goal):
            raise ValueError("goal_adjustments y protein_g_per_kg deben cubrir todos los objetivos")
        if set(self.fat_g_per_kg) != set(Goal):
            raise ValueError("fat_g_per_kg debe cubrir todos los objetivos")
        if set(self.kcal_floor) != set(Sex):
            raise ValueError("kcal_floor debe cubrir todos los sexos")
        for name, values, (lo, hi) in (
            ("protein_g_per_kg", self.protein_g_per_kg, PROTEIN_G_PER_KG_RANGE),
            ("fat_g_per_kg", self.fat_g_per_kg, FAT_G_PER_KG_RANGE),
        ):
            for goal, value in values.items():
                if not lo <= value <= hi:
                    raise ValueError(
                        f"{name}[{goal.value}] = {value} fuera del rango [{lo}, {hi}]"
                    )
        if set(self.meal_distribution) != set(MealSlot):
            raise ValueError("meal_distribution debe cubrir los 5 slots")
        total = sum(self.meal_distribution.values())
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"meal_distribution debe sumar 1.0 (suma {total})")
        return self
