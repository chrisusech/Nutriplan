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

Aquí vive SOLO la elección. Qué platos son cocinables lo decide
`template_pools`; cuánto cuesta cada decisión, `template_costs`.
"""

from collections import Counter
from collections.abc import Callable
from math import ceil
from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel

from nutriplan.adapters.llm.template_costs import (
    SLOT_ORDER,
    VARIETY_CATEGORIES,
    W_ANCHOR_DAY,
    W_AVOID_FOOD,
    W_DROPPED,
    W_EN_CASA,
    W_FAT_OVER,
    W_FOOD_WEEK,
    W_LOVED,
    W_OVER_CAP,
    W_PREFER_FOOD,
    W_RECENCY,
    W_RECENT_TEMPLATE,
    W_RECENT_WEEK,
    W_REJECTED,
    W_SAME_DAY,
    W_TEMPLATE,
    W_UNDER_CARB,
    affinity_cost,
)
from nutriplan.adapters.llm.template_pools import (
    DishFilter,
    InsufficientDishes,
    SlotTargets,
    build_pools,
)
from nutriplan.domain.dish_recipe import dish_key
from nutriplan.domain.errors import LLMError
from nutriplan.domain.generation_rules import CARB_GROUP, slot_availability
from nutriplan.domain.meal_template import Dish, MealCatalog, expand, pool_health
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealSlot,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import can_cover_carb, max_carb_g, usable_in_slot
from nutriplan.domain.swap_note import foods_from_note
from nutriplan.domain.taste import TasteProfile

T = TypeVar("T", bound=BaseModel)

__all__ = ["InsufficientDishes", "TemplateSelector"]


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
        on_hand_ids: frozenset[UUID] = frozenset(),
        taste: TasteProfile | None = None,
        recent_keys: frozenset[str] = frozenset(),
        recent_templates: frozenset[str] = frozenset(),
    ) -> None:
        targets = SlotTargets.build(daily, config)
        # Las comidas del cliente: las que su reparto declara, en el orden del día.
        self.slots = [s for s in SLOT_ORDER if s in targets.protein]
        self._allowed = allowed
        self._catalog = catalog
        self._on_hand_ids = on_hand_ids
        self._filter = DishFilter(targets)
        self._food_ok = self._filter.food_ok

        # Lo de casa, en el mismo formato que `cost` maneja los ids.
        self._en_casa = {str(fid) for fid in on_hand_ids}
        self._taste = taste
        self._rejected_keys = set(taste.rejected_keys) if taste else set()
        self._loved_keys = set(taste.loved_keys) if taste else set()
        self._rejected_templates = set(taste.rejected_templates) if taste else set()
        self._loved_templates = set(taste.loved_templates) if taste else set()
        self._rejected_names = {n.lower() for n in (taste.rejected_dishes if taste else [])}
        self._loved_names = {n.lower() for n in (taste.loved_dishes if taste else [])}
        self._avoid = {str(fid) for fid in (taste.avoid_food_ids if taste else [])}
        self._prefer = {str(fid) for fid in (taste.prefer_food_ids if taste else [])}
        self._recent_keys = set(recent_keys)
        self._recent_templates = set(recent_templates)

        self.pools = build_pools(
            catalog=catalog,
            allowed=allowed,
            filt=self._filter,
            slots=self.slots,
            on_hand_ids=on_hand_ids,
        )
        self._carb_targets = {slot: targets.carb.get(slot, 0.0) for slot in self.slots}
        self.warnings = pool_health({s: self.pools[s] for s in self.slots})
        self._seed = seed
        self.calls = 0
        # Los platos de la última semana elegida, por (día, comida).
        self.last_dishes: dict[tuple[int, MealSlot], Dish] = {}

        # El tope por slot que `check_variety` va a aplicar. El motor lo usa para
        # no pasarse: si no lo conoce, produce planes que la validación rechaza.
        gen = config.generation if config else None
        self._cap = {
            FoodCategory.PROTEIN: gen.max_protein_repeats_per_week if gen else 4,
            FoodCategory.DAIRY: gen.max_protein_repeats_per_week if gen else 4,
            FoodCategory.CARB: gen.max_carb_repeats_per_week if gen else 4,
            FoodCategory.FRUIT: gen.max_carb_repeats_per_week if gen else 4,
        }
        # Cuántas opciones REALES tiene el cliente en cada slot — las mismas que
        # cuenta `check_variety`, que es quien lo va a juzgar. Con dos carbos de
        # desayuno para siete días, repetir cuatro veces es inevitable y no hay que
        # penalizarlo.
        self._options = slot_availability(
            allowed, usable_in_slot(daily, config) if config else None
        )

        # El PRESUPUESTO DE GRASA del día, y cuánta arrastra cada plato sin remedio.
        #
        # El motor elegía por variedad y por afinidad, y no tenía ni idea de que la
        # grasa del día es finita. Así se armaban días imposibles: una carne grasa al
        # almuerzo con aguacate, y una crema de frutos secos al desayuno, ya se comen
        # los 50 g de grasa de un día en déficit — y entonces el reparador recorta la
        # PROTEÍNA para bajar las kcal, y la comida se queda sin ella. El fallo salía
        # como "al almuerzo le faltan 11 g de proteína", que no es donde está el
        # problema, y solo después de agotar los cuatro intentos.
        self._fat_budget = daily.fat_g
        self._dish_fat = {
            id(dish): self._filter.floor_fat(dish) for pool in self.pools.values() for dish in pool
        }

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        raise LLMError(
            "La ingesta de documentos Word necesita el LLM real: configura "
            "ANTHROPIC_API_KEY en el .env (el modo offline solo genera planes)."
        )

    def pop_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "calls": self.calls}

    def _cap_for(self, slot: MealSlot, category: FoodCategory, ndays: int) -> int:
        """El techo que `check_variety` va a aplicar a este alimento en este slot.

        Si el cliente tiene menos opciones que días, repetir es INEVITABLE y el
        techo se relaja: con dos carbos de desayuno y siete días, alguno sale
        cuatro veces y no es culpa del motor.
        """
        options = self._options.get((slot, category), 0) or 1
        return max(self._cap.get(category, 4), ceil(ndays / options))

    def _choice_order(self, day: int) -> list[MealSlot]:
        """Quién elige plato primero hoy. El día se SIRVE siempre en orden.

        Elegir en el mismo orden en que se come condena a la cena: cuando le
        toca, lo que quería ya se lo comió otra comida y repetirlo el mismo día
        es lo más caro del sistema. En el beta salieron 27 cenas del mismo plato
        contra 1 del otro, y el motivo no era que gustara más, sino que era el
        único que quedaba en pie a las ocho de la noche. Turnándose, el coste de
        compartir un alimento se reparte en vez de caer siempre en la última.
        """
        orden = list(self.slots)
        if day % 2 == 0:
            return orden
        try:
            i, j = orden.index(MealSlot.LUNCH), orden.index(MealSlot.DINNER)
        except ValueError:
            return orden
        orden[i], orden[j] = orden[j], orden[i]
        return orden

    def select_week(self, *, seed: int, ndays: int = 7) -> list[dict[MealSlot, Dish]]:
        used_template: Counter[str] = Counter()
        used_dish: Counter[str] = Counter()
        used_names: Counter[str] = Counter()
        used_food: Counter[str] = Counter()  # CRUZA SLOTS: aquí muere el yogur 14×
        used_in_slot: Counter[tuple[str, MealSlot]] = Counter()  # como cuenta el validador
        last_day: dict[str, int] = {}
        week: list[dict[MealSlot, Dish]] = []

        for day in range(ndays):
            today_foods: set[str] = set()
            today_anchors: set[str] = set()
            today_fat = 0.0  # la grasa que los platos de hoy ya arrastran
            chosen: dict[MealSlot, Dish] = {}

            for slot in self._choice_order(day):
                pool = self.pools[slot]

                def cost(
                    dish: Dish,
                    *,
                    _today: set[str] = today_foods,
                    _anchors: set[str] = today_anchors,
                    _day: int = day,
                    _slot: MealSlot = slot,
                    _fat: Callable[[], float] = lambda: today_fat,
                ) -> float:
                    ids = {str(fid) for fid in dish.food_ids}
                    total = W_SAME_DAY * len(ids & _today)
                    total += W_DROPPED * dish.dropped
                    # Aprovechar lo que ya está comprado: descuento, no orden.
                    total -= W_EN_CASA * len(ids & self._en_casa)
                    total += self._taste_cost(dish, ids)
                    key = dish_key(dish.template_id, list(dish.food_ids))
                    total += W_RECENT_WEEK * used_dish[key] ** 2
                    total += W_RECENT_WEEK * used_names[dish.name.lower()] ** 2
                    if key in self._recent_keys:
                        total += W_RECENT_WEEK
                    if dish.template_id in self._recent_templates:
                        total += W_RECENT_TEMPLATE
                    # Lo que este plato haría al presupuesto de grasa del día. Solo
                    # se cobra lo que se PASA: mientras quepa, la grasa no opina.
                    over_fat = _fat() + self._dish_fat.get(id(dish), 0.0) - self._fat_budget
                    if over_fat > 0:
                        total += W_FAT_OVER * over_fat
                    total += W_TEMPLATE * used_template[dish.template_id] ** 2
                    if str(dish.anchor.id) in _anchors:
                        total += W_ANCHOR_DAY
                    carb_t = self._carb_targets.get(_slot, 0.0)
                    techo = sum(
                        max_carb_g(f)
                        for f in dish.foods
                        if f.category in CARB_GROUP and f.carb_100g > 0
                    )
                    conjunto_cubre = carb_t <= 0 or techo >= carb_t
                    for food in dish.foods:
                        # La afinidad se cobra a TODOS los alimentos, no sólo a los
                        # que cuentan para variedad: si alguien declara que un
                        # aceite es de cocina y no de desayuno, hay que hacerle caso.
                        total += affinity_cost(food, _slot)
                        if (
                            food.category is FoodCategory.CARB
                            and not conjunto_cubre
                            and not can_cover_carb(food, carb_t)
                        ):
                            total += W_UNDER_CARB
                        if food.category not in VARIETY_CATEGORIES:
                            continue
                        fid = str(food.id)
                        total += W_FOOD_WEEK * used_food[fid] ** 2
                        gap = _day - last_day.get(fid, -99)
                        total += W_RECENCY * max(0, 3 - gap)
                        # No pasarse del tope con el que lo van a validar.
                        cap = self._cap_for(_slot, food.category, ndays)
                        over = used_in_slot[(fid, _slot)] + 1 - cap
                        if over > 0:
                            total += W_OVER_CAP * over
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
                today_fat += self._dish_fat.get(id(best), 0.0)
                used_template[best.template_id] += 1
                used_dish[dish_key(best.template_id, list(best.food_ids))] += 1
                used_names[best.name.lower()] += 1
                today_anchors.add(str(best.anchor.id))
                for food in best.foods:
                    fid = str(food.id)
                    used_food[fid] += 1
                    used_in_slot[(fid, slot)] += 1
                    last_day[fid] = day
                    today_foods.add(fid)

            week.append(chosen)
        return week

    def _taste_cost(self, dish: Dish, ids: set[str]) -> float:
        """Veto y cariño del historial. Vacío = 0, como quien acaba de llegar."""
        if self._taste is None or self._taste.is_empty:
            return 0.0
        key = dish_key(dish.template_id, list(dish.food_ids))
        total = 0.0
        if (
            key in self._rejected_keys
            or dish.template_id in self._rejected_templates
            or dish.name.lower() in self._rejected_names
        ):
            total += W_REJECTED
        if (
            key in self._loved_keys
            or dish.template_id in self._loved_templates
            or dish.name.lower() in self._loved_names
        ):
            total -= W_LOVED
        total += W_AVOID_FOOD * len(ids & self._avoid)
        total -= W_PREFER_FOOD * len(ids & self._prefer)
        return total

    def rank_slot(
        self,
        slot: MealSlot,
        *,
        exclude_keys: frozenset[str] = frozenset(),
        today_ids: frozenset[str] = frozenset(),
        note: str = "",
    ) -> list[Dish]:
        """Candidatos de un slot, baratos primero. Para cambiar un solo plato.

        Una nota no vacía FILTRA por alimentos (nombre y alias). El pool de la
        semana solo guarda las 6 proteínas más afines: si pide pollo y el pollo
        no cabía en ese corte, se reexpande anclándolo. Si nadie cubre lo
        pedido, la lista queda vacía: el borde le dice la verdad a la persona.
        """
        wanted = foods_from_note(note, self._allowed) if note.strip() else []
        pool = self.pools.get(slot, [])
        if note.strip():
            pinned = frozenset(food.id for food in wanted)
            if not pinned:
                return []
            # El filtro de kcal del pool semanal echa al pollo si el pescado
            # aterriza más fácil. Quien pide pollo merece que el solver lo intente.
            pool = expand(
                self._catalog,
                self._allowed,
                admissible=self._food_ok,
                dish_admissible=lambda _dish: True,
                on_hand=self._on_hand_ids | pinned,
            ).get(slot, [])
        wanted_ids = {food.id for food in wanted}
        ranked: list[tuple[float, Dish]] = []
        for dish in pool:
            key = dish_key(dish.template_id, list(dish.food_ids))
            if key in exclude_keys:
                continue
            if wanted_ids and not wanted_ids <= set(dish.food_ids):
                continue
            ids = {str(fid) for fid in dish.food_ids}
            cost = W_SAME_DAY * len(ids & today_ids) + self._taste_cost(dish, ids)
            ranked.append((cost, dish))
        ranked.sort(key=lambda pair: (pair[0], pair[1].name))
        return [dish for _, dish in ranked]

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        seed = self._seed + self.calls  # el reintento desplaza el desempate
        self.calls += 1
        week = self.select_week(seed=seed)
        # El schema del LLM solo lleva food_ids —un modelo no conoce los ids de
        # plantilla—, así que la identidad del plato se guarda al lado y la lee
        # `_selection_to_days`. Sin esto, "Tostada de huevos con aguacate" se
        # perdía y quedaba la lista de alimentos.
        self.last_dishes = {
            (i, slot): dish for i, day in enumerate(week) for slot, dish in day.items()
        }
        days = [
            {
                "day_index": i,
                "meals": [
                    {
                        "slot": slot.value,
                        "food_ids": [str(fid) for fid in day[slot].food_ids],
                        "free_salad": day[slot].free_salad,
                    }
                    for slot in self.slots
                ],
            }
            for i, day in enumerate(week)
        ]
        return schema.model_validate({"days": days})
