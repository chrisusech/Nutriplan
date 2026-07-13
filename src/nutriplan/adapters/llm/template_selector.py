"""Motor de platos: elige COMBINACIONES coherentes, no alimentos sueltos.

Sustituye al `HeuristicSelector` cuando el catálogo de platos alcanza para el
cliente. La diferencia de fondo: el heurístico armaba cada comida rol por rol
sobre listas ordenadas alfabéticamente —el orden del nombre decidía qué se juntaba
con qué—, y aquí la unidad de elección es el PLATO, que ya viene aprobado por un
humano. "Yogur + pan" deja de ser una comida posible porque nadie escribió ese
plato.

La selección es greedy y determinista, con una función de coste que penaliza la
repetición. NO hay aritmética modular en el camino principal: ahí vivía el bug que
condenaba a un cliente a comer el mismo yogur los siete días (con un pool de
tamaño par, el índice `(2·día + offset) % len` es constante toda la semana).
"""

from collections import Counter
from typing import TypeVar

from pydantic import BaseModel

from nutriplan.domain.errors import LLMError
from nutriplan.domain.meal_template import (
    Dish,
    MealCatalog,
    expand,
    pool_health,
)
from nutriplan.domain.models import FoodCategory, FoodItem, MacroTargets, MealSlot
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import fits_protein

T = TypeVar("T", bound=BaseModel)

_FALLBACK_SHARE = {
    MealSlot.BREAKFAST: 0.25,
    MealSlot.SNACK_AM: 0.10,
    MealSlot.LUNCH: 0.30,
    MealSlot.SNACK_PM: 0.10,
    MealSlot.DINNER: 0.25,
}

SLOT_ORDER = [
    MealSlot.BREAKFAST,
    MealSlot.SNACK_AM,
    MealSlot.LUNCH,
    MealSlot.SNACK_PM,
    MealSlot.DINNER,
]

# Pesos del coste. El del mismo día es una prohibición de facto: repetir un
# alimento dos veces en una jornada es peor que cualquier otra cosa.
W_SAME_DAY = 10_000.0
W_TEMPLATE = 120.0   # repetir el mismo plato en la semana
W_FOOD_WEEK = 40.0   # repetir el mismo alimento en la semana, CRUZANDO SLOTS
W_ANCHOR_DAY = 80.0  # el ancla proteica del día, repetida en otro slot
W_RECENCY = 25.0     # comer hoy lo de ayer o anteayer

# Las grasas son condimento: que el aceite de oliva salga todos los almuerzos no
# es falta de variedad. Lo que define la comida son la proteína, el carbo y la fruta.
VARIETY_CATEGORIES = frozenset(
    {FoodCategory.PROTEIN, FoodCategory.DAIRY, FoodCategory.CARB, FoodCategory.FRUIT}
)


class InsufficientDishes(LLMError):
    """La lista del cliente no da para armar ningún plato en algún slot."""


class TemplateSelector:
    """Implementa el puerto LLMClient (solo select_plan)."""

    def __init__(
        self,
        allowed: list[FoodItem],
        catalog: MealCatalog,
        daily: MacroTargets,
        *,
        seed: int = 0,
        config: NutritionConfig | None = None,
    ) -> None:
        share = dict(config.meal_distribution) if config else _FALLBACK_SHARE
        targets = {slot: daily.protein_g * share[slot] for slot in MealSlot}

        def admissible(food: FoodItem, slot: MealSlot) -> bool:
            return fits_protein(food, targets[slot])

        self.pools = expand(catalog, allowed, admissible=admissible)
        empty = [s.value for s in SLOT_ORDER if not self.pools[s]]
        if empty:
            raise InsufficientDishes(
                "No hay ningún plato que se pueda cocinar con los alimentos de este "
                f"cliente en: {', '.join(empty)}."
            )
        self.warnings = pool_health(self.pools)
        self._seed = seed
        self.calls = 0

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        raise LLMError(
            "La ingesta de documentos Word necesita el LLM real: configura "
            "ANTHROPIC_API_KEY en el .env (el modo offline solo genera planes)."
        )

    def pop_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "calls": self.calls}

    def select_week(self, *, seed: int, ndays: int = 7) -> list[dict[MealSlot, Dish]]:
        used_template: Counter[str] = Counter()
        used_food: Counter[str] = Counter()  # CRUZA SLOTS: aquí muere el yogur 14×
        last_day: dict[str, int] = {}
        week: list[dict[MealSlot, Dish]] = []

        for day in range(ndays):
            today_foods: set[str] = set()
            today_anchors: set[str] = set()
            chosen: dict[MealSlot, Dish] = {}

            for slot in SLOT_ORDER:
                pool = self.pools[slot]

                def cost(dish: Dish, *, _today=today_foods, _anchors=today_anchors,
                         _day=day) -> float:
                    ids = {str(fid) for fid in dish.food_ids}
                    total = W_SAME_DAY * len(ids & _today)
                    total += W_TEMPLATE * used_template[dish.template_id] ** 2
                    if str(dish.anchor.id) in _anchors:
                        total += W_ANCHOR_DAY
                    for food in dish.foods:
                        if food.category not in VARIETY_CATEGORIES:
                            continue
                        fid = str(food.id)
                        total += W_FOOD_WEEK * used_food[fid] ** 2
                        gap = _day - last_day.get(fid, -99)
                        total += W_RECENCY * max(0, 3 - gap)
                    return total

                # El término modular SOLO desempata platos de coste IDÉNTICO, y el
                # coste cambia en cuanto los contadores se llenan. Por eso no puede
                # degenerar en un índice constante como la rotación que sustituye.
                best = min(
                    enumerate(pool),
                    key=lambda pair: (
                        cost(pair[1]),
                        (pair[0] + seed * 31 + day * 7) % len(pool),
                        pair[1].key,
                    ),
                )[1]

                chosen[slot] = best
                used_template[best.template_id] += 1
                today_anchors.add(str(best.anchor.id))
                for food in best.foods:
                    fid = str(food.id)
                    used_food[fid] += 1
                    last_day[fid] = day
                    today_foods.add(fid)

            week.append(chosen)
        return week

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        seed = self._seed + self.calls  # el reintento desplaza el desempate
        self.calls += 1
        week = self.select_week(seed=seed)
        days = [
            {
                "day_index": i,
                "meals": [
                    {
                        "slot": slot.value,
                        "food_ids": [str(fid) for fid in day[slot].food_ids],
                        "free_salad": day[slot].free_salad,
                    }
                    for slot in SLOT_ORDER
                ],
            }
            for i, day in enumerate(week)
        ]
        return schema.model_validate({"days": days})
