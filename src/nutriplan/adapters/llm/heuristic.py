"""Selector heurístico determinista — modo offline del puerto LLMClient.

Construye una selección estructuralmente válida rotando proteínas/carbos por
día, sin tocar la red. Es el adaptador de generación cuando no hay
ANTHROPIC_API_KEY (y el doble de prueba en tests): la generación real solo
cambia el adaptador (HeuristicSelector ↔ AnthropicClient).

La extracción de ingesta no tiene modo offline: entender un Word libre
requiere el LLM real, así que `extract` falla con un LLMError claro.
"""

from pydantic import BaseModel

from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot


class HeuristicSelector:
    """Implementa el puerto LLMClient (solo select_plan)."""

    def __init__(self, allowed: list[FoodItem]) -> None:
        def by_cat(c: FoodCategory) -> list[FoodItem]:
            return sorted((f for f in allowed if f.category == c), key=lambda f: f.name_es)

        self.proteins = by_cat(FoodCategory.PROTEIN)
        self.dairy = by_cat(FoodCategory.DAIRY)
        self.carbs = by_cat(FoodCategory.CARB)
        self.fruits = by_cat(FoodCategory.FRUIT)
        self.fats = by_cat(FoodCategory.FAT)
        self.calls = 0

    async def extract(
        self, *, system: str, text: str, schema: type[BaseModel], model: str
    ) -> BaseModel:
        raise LLMError(
            "La ingesta de documentos Word necesita el LLM real: configura "
            "ANTHROPIC_API_KEY en el .env (el modo offline solo genera planes)."
        )

    def pop_usage(self) -> dict:
        return {"input_tokens": 0, "output_tokens": 0, "calls": self.calls}

    async def select_plan(
        self, *, system: str, prompt: str, schema: type[BaseModel], model: str
    ) -> BaseModel:
        self.calls += 1
        P, D, C, F = self.proteins, self.dairy, self.carbs, self.fruits
        if not P or not C or not F or not (D or P):
            raise LLMError(
                "El conjunto permitido no tiene fuentes suficientes "
                "(se requieren proteínas, carbohidratos y frutas)."
            )
        egg = next((f for f in P if "huevo" in f.name_es), None)
        breakfast_protein = egg or P[0]
        snack_protein = (D or P)[0]

        days = []
        for i in range(7):
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
            days.append({"day_index": i, "meals": meals})
        return schema.model_validate({"days": days})
