"""Doble de prueba del LLM para generación: selector determinista.

Construye una selección válida rotando proteínas/carbos por día — imita lo
que haría Sonnet sin tocar la red. La generación real solo cambia el
adaptador (MockLLMClient/HeuristicSelector ↔ AnthropicClient).
"""

from pydantic import BaseModel

from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot


class HeuristicSelector:
    """Implementa el puerto LLMClient (solo select_plan)."""

    def __init__(self, allowed: list[FoodItem]) -> None:
        by_cat = lambda c: sorted(  # noqa: E731
            (f for f in allowed if f.category == c), key=lambda f: f.name_es
        )
        self.proteins = by_cat(FoodCategory.PROTEIN)
        self.dairy = by_cat(FoodCategory.DAIRY)
        self.carbs = by_cat(FoodCategory.CARB)
        self.fruits = by_cat(FoodCategory.FRUIT)
        self.fats = by_cat(FoodCategory.FAT)
        self.calls = 0

    async def extract(self, *, system, text, schema, model):  # pragma: no cover
        raise NotImplementedError

    def pop_usage(self) -> dict:
        return {"input_tokens": 0, "output_tokens": 0, "calls": self.calls}

    async def select_plan(
        self, *, system: str, prompt: str, schema: type[BaseModel], model: str
    ) -> BaseModel:
        self.calls += 1
        P, D, C, F = self.proteins, self.dairy, self.carbs, self.fruits
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
