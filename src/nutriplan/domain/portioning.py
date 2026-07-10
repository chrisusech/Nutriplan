"""Portion solver v1 por roles (sección 11.3) — determinista, sin IA.

Estrategia:
1. Proteína y carbohidratos se resuelven POR SLOT contra el reparto de la
   config (cada alimento cubre su macro dominante; se ajustan los aportes
   cruzados por punto fijo).
2. La grasa cierra A NIVEL DE DÍA: los ítems de grasa explícitos absorben la
   grasa faltante tras contar la que ya traen proteínas y carbos. (Un snack
   de yogur+fruta no puede aportar la grasa de su % de kcal; esa cuota vive
   donde hay fuente real — instancia válida de la estrategia de la spec.)
3. Redondeo a múltiplos de grams_rounding, respetando min_portion_g.

El código es dueño de los números: `computed` SIEMPRE se recalcula desde los
gramos finales y la base de alimentos.
"""

from dataclasses import dataclass

from nutriplan.domain.errors import GenerationError
from nutriplan.domain.generation_rules import (
    CARB_GROUP,
    FAT_GROUP,
    PROTEIN_GROUP,
    SLOT_STRUCTURE,
)
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealFoodPortion,
    MealSlot,
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import NutritionConfig

MAX_PORTION_G = 600.0
FIXED_POINT_ITERATIONS = 10


@dataclass
class SolvedMeal:
    slot: MealSlot
    portions: list[MealFoodPortion]
    computed: MacroTargets


def _round_portion(
    grams: float, cfg: NutritionConfig, food: FoodItem, *, is_fat: bool = False
) -> float:
    """Redondea la porción a algo que un humano sirve de verdad.

    Alimentos contables (huevo, lata de atún, aguacate, pan) se cuantizan a la
    unidad — enteros o medios según su `unit_granularity` — así el plan nunca
    pide '5.5 huevos'. El resto sigue en gramos libres (múltiplos de 5 g).
    """
    if food.unit_granularity is not UnitGranularity.GRAMS and food.default_unit_g:
        unit = food.default_unit_g
        step_g = unit if food.unit_granularity is UnitGranularity.WHOLE else unit / 2.0
        n = round(grams / step_g)
        if n <= 0:
            return 0.0  # el llamador la descarta; una comida no lleva 0.4 huevos
        return float(min(n * step_g, MAX_PORTION_G))

    step = cfg.portioning.grams_rounding
    rounded = round(grams / step) * step
    if rounded <= 0:
        return 0.0
    # Las grasas puras (aceites) admiten porciones pequeñas (una cucharada ≈ 15 g):
    # forzarlas al mínimo general de 20 g deja días en una zona muerta donde ni
    # con ni sin el ítem cuadra la grasa.
    floor = step if is_fat else cfg.portioning.min_portion_g
    return float(min(max(rounded, floor), MAX_PORTION_G))


def macros_of(portions: list[tuple[FoodItem, float]]) -> MacroTargets:
    """Macros de una comida a partir de (alimento, gramos). El código es dueño
    de los números: la UI la reusa al editar porciones en línea."""
    def total(attr: str) -> float:
        return round(sum(float(getattr(f, attr)) * g / 100.0 for f, g in portions), 1)

    return MacroTargets(
        kcal=total("kcal_100g"),
        protein_g=total("protein_100g"),
        carb_g=total("carb_100g"),
        fat_g=total("fat_100g"),
    )


def solve_day_portions(
    meals: list[tuple[MealSlot, list[FoodItem]]],
    daily: MacroTargets,
    config: NutritionConfig,
) -> list[SolvedMeal]:
    """Resuelve los gramos de todos los slots de un día."""
    distribution = config.meal_distribution

    # --- Paso 1: proteína y carbo por slot (punto fijo sobre aportes cruzados)
    grams: dict[tuple[MealSlot, str], float] = {}
    foods_of: dict[MealSlot, list[FoodItem]] = {}
    for slot, foods in meals:
        foods_of[slot] = foods
        for f in foods:
            grams[(slot, str(f.id))] = 0.0

    def slot_sources(slot: MealSlot, group: set[FoodCategory]) -> list[FoodItem]:
        return [f for f in foods_of[slot] if f.category in group]

    # Un slot sin fuente real de su macro obligatorio no se puede resolver:
    # error claro aquí en vez de un plan que "cuadra" ignorando el slot.
    for slot, _foods in meals:
        rule = SLOT_STRUCTURE[slot]
        if rule.requires_protein and not slot_sources(slot, PROTEIN_GROUP):
            raise GenerationError(f"Slot {slot.value}: sin fuente de proteína")
        if (
            rule.requires_carb
            and not rule.carb_optional
            and not slot_sources(slot, CARB_GROUP)
        ):
            raise GenerationError(f"Slot {slot.value}: sin fuente de carbohidrato")

    fat_items = [
        (slot, f) for slot, foods in meals for f in foods if f.category in FAT_GROUP
    ]

    for _ in range(FIXED_POINT_ITERATIONS):
        for slot, foods in meals:
            p_target = daily.protein_g * distribution[slot]
            c_target = daily.carb_g * distribution[slot]

            p_sources = slot_sources(slot, PROTEIN_GROUP)
            c_sources = slot_sources(slot, CARB_GROUP)

            # aportes cruzados con los gramos actuales de las otras fuentes
            p_cross = sum(
                f.protein_100g * grams[(slot, str(f.id))] / 100.0
                for f in foods
                if f not in p_sources
            )
            c_cross = sum(
                f.carb_100g * grams[(slot, str(f.id))] / 100.0
                for f in foods
                if f not in c_sources
            )

            p_needed = max(p_target - p_cross, 0.0)
            c_needed = max(c_target - c_cross, 0.0)

            for sources, needed, attr in (
                (p_sources, p_needed, "protein_100g"),
                (c_sources, c_needed, "carb_100g"),
            ):
                if not sources:
                    continue
                share = needed / len(sources)
                for f in sources:
                    density = getattr(f, attr) / 100.0
                    if density <= 0:
                        raise GenerationError(
                            f"{f.name_es} no aporta {attr}; selección inválida"
                        )
                    grams[(slot, str(f.id))] = min(share / density, MAX_PORTION_G)

        # --- Paso 2 (dentro del punto fijo): la grasa cierra a nivel de día.
        # Un ítem de grasa puede traer proteína/carbo (maní, aguacate); al
        # iterar, las fuentes de arriba compensan ese aporte cruzado.
        fat_so_far = sum(
            f.fat_100g * grams[(slot, str(f.id))] / 100.0
            for slot, foods in meals
            for f in foods
            if f.category not in FAT_GROUP
        )
        fat_needed = daily.fat_g - fat_so_far
        if fat_items:
            share = max(fat_needed, 0.0) / len(fat_items)
            for slot, f in fat_items:
                grams[(slot, str(f.id))] = min(share / (f.fat_100g / 100.0), MAX_PORTION_G)

    # --- Paso 3: redondeo y recálculo (el código es dueño de los números)
    solved: list[SolvedMeal] = []
    for slot, foods in meals:
        final: list[tuple[FoodItem, float]] = []
        for f in foods:
            g = _round_portion(grams[(slot, str(f.id))], config, f,
                               is_fat=f.category in FAT_GROUP)
            if g > 0:
                final.append((f, g))
        if not final:
            raise GenerationError(f"Slot {slot.value}: ninguna porción resuelta")
        solved.append(
            SolvedMeal(
                slot=slot,
                portions=[MealFoodPortion(food_id=f.id, grams=g) for f, g in final],
                computed=macros_of(final),
            )
        )
    return solved
