"""Selector heurístico determinista — modo offline del puerto LLMClient.

Construye una selección estructuralmente válida rotando proteínas/carbos por
día, sin tocar la red. Es el adaptador de generación cuando no hay
ANTHROPIC_API_KEY (y el doble de prueba en tests): la generación real solo
cambia el adaptador (HeuristicSelector ↔ AnthropicClient).

La extracción de ingesta no tiene modo offline: entender un Word libre
requiere el LLM real, así que `extract` falla con un LLMError claro.
"""

from typing import TypeVar

from pydantic import BaseModel

from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot

T = TypeVar("T", bound=BaseModel)


class HeuristicSelector:
    """Implementa el puerto LLMClient (solo select_plan)."""

    def __init__(self, allowed: list[FoodItem]) -> None:
        def by_cat(c: FoodCategory) -> list[FoodItem]:
            return sorted((f for f in allowed if f.category == c), key=lambda f: f.name_es)

        def dense(items: list[FoodItem], attr: str, minimum: float,
                  min_count: int = 1) -> list[FoodItem]:
            """Sin feedback del validador, el heurístico solo usa fuentes densas:
            una legumbre como 'proteína' o un carbo flojo no cuadran objetivos
            altos (tope de 600 g por porción). El filtro se relaja si deja menos
            de min_count opciones (la variedad exige ≥3 por slot a la semana)."""
            filtered = [f for f in items if getattr(f, attr) >= minimum]
            return filtered if len(filtered) >= min_count else items

        self.proteins = dense(by_cat(FoodCategory.PROTEIN), "protein_100g", 12.0, min_count=3)
        # almuerzo/cena: proteínas magras — las grasas del día viven en los
        # ítems de grasa. Lo que importa es grasa POR gramo de proteína:
        # cubrir la cena con huevo (0.75 g/g) mete ~16 g de grasa extra.
        lean = [
            f for f in self.proteins
            if f.fat_100g <= 0.4 * f.protein_100g and "batido" not in f.tags
        ]  # sin batidos: whey de plato principal no es comida real
        # la variedad exige ≥3 proteínas distintas por slot en la semana
        self.main_proteins = lean if len(lean) >= 3 else self.proteins
        # snacks: lácteo denso en proteína y bajo en grasa (yogur griego);
        # leche exige volúmenes con carbo de sobra y el queso suma grasa ×2
        dairy = dense(by_cat(FoodCategory.DAIRY), "protein_100g", 8.0)
        self.dairy = [f for f in dairy if f.fat_100g <= 5.0] or dairy
        self.carbs = dense(by_cat(FoodCategory.CARB), "carb_100g", 20.0, min_count=3)
        self.fruits = by_cat(FoodCategory.FRUIT)
        # grasas con mucha proteína (maní, almendras) desbalancean el desayuno
        fats = by_cat(FoodCategory.FAT)
        self.fats = [f for f in fats if f.protein_100g <= 10.0] or fats
        self.calls = 0  # también desplaza la rotación: cada reintento explora otra combinación

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        raise LLMError(
            "La ingesta de documentos Word necesita el LLM real: configura "
            "ANTHROPIC_API_KEY en el .env (el modo offline solo genera planes)."
        )

    def pop_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "calls": self.calls}

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        offset = self.calls  # reintento n → rotación distinta (el LLM real usa el feedback)
        self.calls += 1
        P, D, C, F = self.main_proteins, self.dairy, self.carbs, self.fruits
        if not P or not C or not F or not (D or P):
            raise LLMError(
                "El conjunto permitido no tiene fuentes suficientes "
                "(se requieren proteínas, carbohidratos y frutas)."
            )
        # el desayuno sí lleva huevo: su grasa reemplaza parte del ítem de grasa
        egg = next((f for f in self.proteins if "huevo entero" in f.name_es), None)
        breakfast_protein = egg or P[0]
        snack_protein = (D or P)[0]

        days = []
        for j in range(7):
            i = j + offset
            breakfast = [breakfast_protein.id, C[i % len(C)].id]
            if self.fats:
                breakfast.append(self.fats[i % len(self.fats)].id)
            meals = [
                {"slot": MealSlot.BREAKFAST.value, "food_ids": [str(x) for x in breakfast]},
                {
                    "slot": MealSlot.SNACK_AM.value,
                    "food_ids": [str(snack_protein.id), str(F[i % len(F)].id)],
                },
                {
                    "slot": MealSlot.LUNCH.value,
                    "food_ids": [str(P[i % len(P)].id), str(C[(i + 1) % len(C)].id)],
                    "free_salad": True,
                },
                {
                    "slot": MealSlot.SNACK_PM.value,
                    "food_ids": [str(snack_protein.id), str(F[(i + 1) % len(F)].id)],
                },
                {
                    "slot": MealSlot.DINNER.value,
                    "food_ids": [str(P[(i + 1) % len(P)].id), str(C[(i + 2) % len(C)].id)],
                    "free_salad": True,
                },
            ]
            days.append({"day_index": j, "meals": meals})
        return schema.model_validate({"days": days})
