"""Schema de la estrategia nutricional (contrato del YAML versionado).

Vive en el dominio porque el motor de cálculo depende de su forma; el YAML en
sí se carga en un adaptador detrás del puerto ConfigProvider.
"""

from collections.abc import Sequence
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator

from nutriplan.domain.models import ActivityLevel, Goal, MealSlot, Sex

# Las comidas que ningún plan puede quitar. Los snacks sí son opcionales: hay
# clientes de cuatro comidas, y de tres.
CORE_SLOTS = (MealSlot.BREAKFAST, MealSlot.LUNCH, MealSlot.DINNER)

MACRO_COLUMNS = ("kcal", "protein_g", "carb_g")


class SlotShare(BaseModel):
    """Cuánto del día vale una comida, MACRO A MACRO.

    Un solo porcentaje por comida no describe ninguna comida real: el desayuno
    lleva mucho carbohidrato y poca proteína, la cena al revés. Con un peso único,
    el desayuno de un cliente sin lácteos (huevos y punto) tenía que aportar 30 g
    de proteína — cinco huevos —, así que el reparador recortaba y el desayuno se
    quedaba corto de todos modos.

    Un escalar en el YAML sigue valiendo: significa el mismo peso para los tres.
    """

    kcal: float = Field(gt=0)
    protein_g: float = Field(ge=0)
    carb_g: float = Field(ge=0)

    @model_validator(mode="before")
    @classmethod
    def _accept_scalar(cls, value: Any) -> Any:
        if isinstance(value, int | float):
            return {"kcal": value, "protein_g": value, "carb_g": value}
        return value


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


class AdaptationConfig(BaseModel):
    """Cómo reacciona el plan al peso semanal. Datos, no ifs en la UI."""

    step_kcal: float = Field(default=150.0, gt=0)
    # Cuánto se puede subir en un solo check-in respecto a las kcal previas.
    max_raise_kcal: float = Field(default=400.0, gt=0)
    # Si |Δpeso| ≤ tolerancia → “vas bien” (hold), no micro-ajustes.
    tolerance_kg: float = Field(default=0.2, ge=0)
    # Por debajo de esto (más negativo) en lose_fat → subir kcal.
    lose_fat_too_fast_kg: float = Field(default=-1.0, lt=0)
    # Por encima en gain_muscle → bajar o frenar el superávit.
    gain_muscle_too_fast_kg: float = Field(default=0.75, gt=0)


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
    meal_distribution: dict[MealSlot, SlotShare]
    tolerances: Tolerances
    portioning: PortioningConfig
    generation: GenerationConfig = GenerationConfig()
    adaptation: AdaptationConfig = AdaptationConfig()

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
                    raise ValueError(f"{name}[{goal.value}] = {value} fuera del rango [{lo}, {hi}]")
        # El reparto puede tener MENOS de cinco comidas (un cliente come cuatro),
        # pero nunca puede quedarse sin desayuno, almuerzo o cena: los snacks son
        # lo único opcional.
        missing = [s.value for s in CORE_SLOTS if s not in self.meal_distribution]
        if missing:
            raise ValueError(f"meal_distribution debe incluir {', '.join(missing)}")
        for macro in MACRO_COLUMNS:
            total = sum(getattr(s, macro) for s in self.meal_distribution.values())
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"meal_distribution[{macro}] debe sumar 1.0 (suma {total})")
        return self

    def kcal_shares(self) -> dict[MealSlot, float]:
        """El peso de cada comida en las kcal del día."""
        return {slot: share.kcal for slot, share in self.meal_distribution.items()}

    def for_slots(self, slots: Sequence[MealSlot]) -> "NutritionConfig":
        """La misma estrategia para un cliente que come SOLO estas comidas.

        Quitar el snack de la tarde no adelgaza el día: su cuota se reparte entre
        las comidas que quedan, en proporción a lo que ya pesaban. Cada macro se
        renormaliza por separado.
        """
        keep = [s for s in self.meal_distribution if s in set(slots)]
        if not keep:
            raise ValueError("Un plan necesita al menos una comida")
        totals = {
            macro: sum(getattr(self.meal_distribution[s], macro) for s in keep)
            for macro in MACRO_COLUMNS
        }
        distribution = {
            slot: SlotShare(
                **{
                    macro: (
                        getattr(self.meal_distribution[slot], macro) / totals[macro]
                        if totals[macro] > 0
                        else 0.0
                    )
                    for macro in MACRO_COLUMNS
                }
            )
            for slot in keep
        }
        return self.model_copy(update={"meal_distribution": distribution})
