"""Selector determinista — el MOTOR PRINCIPAL de generación (la IA es un extra).

Arma una semana estructuralmente válida sin tocar la red: elige, por slot,
alimentos que de verdad cuadran su objetivo de proteína y ROTA entre los días
para que ningún alimento se repita toda la semana (fin del "yogur 14×"). Es
consciente de las unidades: no mete una lata entera de atún en un snack donde
solo caben ~50 g (evita el "0.5 latas" y que el slot quede sin proteína).

A diferencia del AnthropicClient, este selector recibe el objetivo de proteína
diario en el constructor (se arma donde los targets existen), así clasifica qué
alimento cabe en cada slot con números reales, no adivinando desde el prompt.

Cuando hay ANTHROPIC_API_KEY, el AnthropicClient sustituye a este adaptador; la
extracción de intake sí exige el LLM real.
"""

from typing import TypeVar

from pydantic import BaseModel

from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot, UnitGranularity

T = TypeVar("T", bound=BaseModel)

# Debe reflejar config.meal_distribution: cuánta proteína apunta cada slot.
_SLOT_SHARE = {
    MealSlot.BREAKFAST: 0.25,
    MealSlot.SNACK_AM: 0.10,
    MealSlot.LUNCH: 0.30,
    MealSlot.SNACK_PM: 0.10,
    MealSlot.DINNER: 0.25,
}
# Piso de tolerancia en gramos: espeja MIN_RELEVANT_G del validador, así lo que
# el heurístico considera "cabe" es lo que validate_day aceptará por slot.
_PROTEIN_FLOOR_G = 10.0


def _fits_protein(food: FoodItem, target_g: float) -> bool:
    """¿Este alimento puede cubrir la proteína del slot sin ser absurdo?

    Un alimento en gramos siempre cabe (se porciona fino). Uno por unidades
    (huevo, lata) solo cabe si algún número entero/medio de unidades aterriza
    dentro de la tolerancia del slot: 2 huevos ≈ 12 g sirve para un snack; una
    lata de atún (26 g) no — se pasa del objetivo de 12 g.
    """
    if food.unit_granularity is UnitGranularity.GRAMS or not food.default_unit_g:
        return True
    unit_protein = food.protein_100g * food.default_unit_g / 100.0
    if unit_protein <= 0:
        return True  # su rol no es la proteína (pan, aguacate)
    step = 1.0 if food.unit_granularity is UnitGranularity.WHOLE else 0.5
    count = max(round(target_g / unit_protein / step) * step, step)
    tol = max(0.15 * target_g, _PROTEIN_FLOOR_G)
    return abs(count * unit_protein - target_g) <= tol


class HeuristicSelector:
    """Implementa el puerto LLMClient (solo select_plan). Motor principal offline."""

    def __init__(self, allowed: list[FoodItem], daily_protein_g: float = 120.0) -> None:
        def by_cat(c: FoodCategory) -> list[FoodItem]:
            return sorted((f for f in allowed if f.category == c), key=lambda f: f.name_es)

        def dense(items: list[FoodItem], attr: str, minimum: float,
                  min_count: int = 1) -> list[FoodItem]:
            """El motor solo usa fuentes densas: una legumbre como 'proteína' o
            un carbo flojo no cuadran objetivos altos (tope de 600 g/porción).
            Se relaja si dejaría menos de min_count opciones."""
            filtered = [f for f in items if getattr(f, attr) >= minimum]
            return filtered if len(filtered) >= min_count else items

        proteins = dense(by_cat(FoodCategory.PROTEIN), "protein_100g", 12.0)
        # Platos principales: proteínas magras (la grasa del día vive en los ítems
        # de grasa) que cuadren el objetivo del almuerzo con su granularidad.
        lunch_pt = daily_protein_g * _SLOT_SHARE[MealSlot.LUNCH]
        lean = [
            f for f in proteins
            if f.fat_100g <= 0.4 * f.protein_100g and "batido" not in f.tags
        ]
        lean = lean or proteins
        self.main_proteins = [f for f in lean if _fits_protein(f, lunch_pt)] or lean

        # Snacks: proteína que se porciona fino a un objetivo pequeño. Prioriza
        # lácteos bajos en grasa; una lata entera de atún se descarta aquí.
        snack_pt = daily_protein_g * _SLOT_SHARE[MealSlot.SNACK_AM]
        dairy = dense(by_cat(FoodCategory.DAIRY), "protein_100g", 8.0)
        dairy = [f for f in dairy if f.fat_100g <= 5.0] or dairy
        snack_pool = dairy + [f for f in proteins if f not in dairy]
        self.snack_proteins = [f for f in snack_pool if _fits_protein(f, snack_pt)] or snack_pool

        # Desayuno: el huevo cabe (3–4 u); una proteína que cuadre el objetivo medio.
        bfast_pt = daily_protein_g * _SLOT_SHARE[MealSlot.BREAKFAST]
        self.breakfast_proteins = [f for f in proteins if _fits_protein(f, bfast_pt)] or proteins

        self.carbs = dense(by_cat(FoodCategory.CARB), "carb_100g", 20.0)
        self.fruits = by_cat(FoodCategory.FRUIT)
        # Grasas muy proteicas (maní, almendras) desbalancean el desayuno.
        fats = by_cat(FoodCategory.FAT)
        self.fats = [f for f in fats if f.protein_100g <= 10.0] or fats
        self.calls = 0

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        raise LLMError(
            "La ingesta de documentos Word necesita el LLM real: configura "
            "ANTHROPIC_API_KEY en el .env (el modo offline solo genera planes)."
        )

    def pop_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "calls": self.calls}

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        offset = self.calls  # cada reintento explora otra rotación
        self.calls += 1

        Pm, Ps, Pb = self.main_proteins, self.snack_proteins, self.breakfast_proteins
        C, F, Fat = self.carbs, self.fruits, self.fats
        if not Pm or not C or not F or not Ps or not Pb:
            raise LLMError(
                "El conjunto permitido no tiene fuentes suficientes por slot "
                "(se requieren proteínas magras, carbohidratos y frutas)."
            )

        def pick(pool: list[FoodItem], i: int, shift: int = 0) -> FoodItem:
            """Rotación por día: recorre el pool para no repetir el alimento."""
            return pool[(i + shift + offset) % len(pool)]

        days = []
        for i in range(7):
            breakfast = [pick(Pb, i).id, pick(C, i).id]
            if Fat:
                breakfast.append(pick(Fat, i).id)
            meals = [
                {"slot": MealSlot.BREAKFAST.value, "food_ids": [str(x) for x in breakfast]},
                {"slot": MealSlot.SNACK_AM.value,
                 "food_ids": [str(pick(Ps, i).id), str(pick(F, i).id)]},
                {"slot": MealSlot.LUNCH.value,
                 "food_ids": [str(pick(Pm, i).id), str(pick(C, i, shift=1).id)],
                 "free_salad": True},
                {"slot": MealSlot.SNACK_PM.value,
                 "food_ids": [str(pick(Ps, i, shift=1).id), str(pick(F, i, shift=1).id)]},
                {"slot": MealSlot.DINNER.value,
                 "food_ids": [str(pick(Pm, i, shift=1).id), str(pick(C, i, shift=2).id)],
                 "free_salad": True},
            ]
            days.append({"day_index": i, "meals": meals})
        return schema.model_validate({"days": days})
