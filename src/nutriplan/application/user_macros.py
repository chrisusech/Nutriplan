"""Los números que la persona pone a mano: onboarding y perfil, el mismo dato."""

from __future__ import annotations

from nutriplan.domain.adapt_targets import USER_LOCK
from nutriplan.domain.calculation import energy_kcal, kcal_floor_for
from nutriplan.domain.errors import CalculationError
from nutriplan.domain.models import Client, MacroFormula
from nutriplan.domain.nutrition_config import NutritionConfig

# Un desajuste de 30 kcal es ruido de redondeo; más que eso se avisa.
_KCAL_SLACK = 30.0
MIN_MACRO_G = 1.0
MAX_KCAL = 5000.0


def user_macro_plan(
    *,
    client: Client,
    config: NutritionConfig,
    kcal: float,
    protein_g: float,
    carb_g: float,
    fat_g: float,
    adapt_with_weight: bool = False,
) -> tuple[MacroFormula, dict[str, float], str | None]:
    """Valida gramos y kcal, arma la fórmula y el candado USER_LOCK.

    Las kcal reales son 4P+4C+9G. Si lo que escribió no cuadra, se avisa y se
    guardan las kcal de los gramos — no se silencia el descuadre.
    """
    if protein_g < MIN_MACRO_G or fat_g < MIN_MACRO_G or carb_g < 0:
        raise CalculationError("La proteína y la grasa tienen que ser mayores que cero.")
    from_grams = energy_kcal(protein_g, carb_g, fat_g)
    warning: str | None = None
    if abs(from_grams - kcal) > _KCAL_SLACK:
        warning = (
            f"Esos gramos dan {from_grams:.0f} kcal, no {kcal:.0f}. "
            "Guardamos las de los gramos para que el menú cuadre."
        )
        kcal = from_grams
    floor = kcal_floor_for(client, config)
    if kcal < floor:
        raise CalculationError(
            f"{kcal:.0f} kcal está bajo el piso de {floor:.0f} para tu cuerpo. "
            "Sube las calorías o revisa los gramos."
        )
    if kcal > MAX_KCAL:
        raise CalculationError(f"{kcal:.0f} kcal se sale del rango que podemos armar.")
    formula = MacroFormula(
        protein_g_per_kg=round(protein_g / client.weight_kg, 4),
        fat_g_per_kg=round(fat_g / client.weight_kg, 4),
        kcal_override=kcal,
    )
    overrides: dict[str, float] = {
        "protein_g": round(protein_g, 1),
        "carb_g": round(carb_g, 1),
        "fat_g": round(fat_g, 1),
    }
    if not adapt_with_weight:
        overrides[USER_LOCK] = 1.0
    return formula, overrides, warning
