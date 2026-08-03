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
from collections.abc import Callable
from math import ceil
from typing import TypeVar

from pydantic import BaseModel

from nutriplan.domain.errors import LLMError
from nutriplan.domain.generation_rules import (
    CARB_GROUP,
    PROTEIN_GROUP,
    slot_availability,
)
from nutriplan.domain.meal_template import (
    Dish,
    MealCatalog,
    expand,
    pool_health,
)
from nutriplan.domain.models import (
    DEFAULT_SLOT_WEIGHT,
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealSlot,
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import fits_protein, usable_in_slot
from nutriplan.domain.validation import MIN_RELEVANT_G

T = TypeVar("T", bound=BaseModel)

# El suelo de porción de un acompañante que no declara el suyo. Coincide con
# `portioning.min_portion_g` de la config: por debajo, el solver no baja.
MIN_CO_PORTION_G = 20.0

# Peso de cada comida en la PROTEÍNA del día. Solo se usa si no hay config.
_FALLBACK_PROTEIN_SHARE = {
    MealSlot.BREAKFAST: 0.22,
    MealSlot.SNACK_AM: 0.05,
    MealSlot.LUNCH: 0.36,
    MealSlot.SNACK_PM: 0.05,
    MealSlot.DINNER: 0.32,
}

# El orden del día. Los slots que el cliente NO come no salen del plan: el motor
# recorre los que su reparto declara.
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
# Pasarse del tope de repeticiones que `check_variety` va a exigir. Es casi una
# prohibición: sin este término, el motor no conoce la regla por la que lo
# evalúan, y con dos carbos de desayuno para siete días hacía 5/2 en vez de 4/3
# —una sola repetición de más— y la generación fallaba entera.
W_OVER_CAP = 5_000.0
W_TEMPLATE = 120.0   # repetir el mismo plato en la semana
W_FOOD_WEEK = 40.0   # repetir el mismo alimento en la semana, CRUZANDO SLOTS
W_ANCHOR_DAY = 80.0  # el ancla proteica del día, repetida en otro slot
W_RECENCY = 25.0     # comer hoy lo de ayer o anteayer

# La afinidad por comida (`FoodItem.weight_in`) son DOS ideas distintas, y cobrarlas
# igual fue el primer error de calibración:
#
# W_PREFERRED — "es su sitio". Entre el arroz y la papa en un almuerzo no hay drama:
#   una caricia basta para ordenarlos, y la variedad puede pasarle por encima.
#
# W_OFF_MEAL — "no es su comida": el pan y la arepa en una cena. Esto NO es una
#   preferencia, es un error de cocina, y tiene que ganarle a la variedad. Con la
#   lista corta de un cliente real (tres carbos para catorce comidas principales)
#   repetir es inevitable, y repetir arroz cuatro veces es lo que haría cualquier
#   nutricionista antes que mandar pan a la cena. Por eso 2.500 y no 30: el coste de
#   repetir un alimento es 40·n² (40, 160, 360... 1.960 a la séptima), así que el
#   motor agota los carbos buenos —hasta el techo de variedad— antes de tocar el pan.
#   Se queda por debajo de W_OVER_CAP a propósito: cuando repetir más ROMPERÍA la
#   regla de variedad, el pan vuelve a ser la salida buena.
#
# El que no declara peso (el catálogo entero, hasta ahora) paga 0. Un plato sin datos
# de afinidad cuesta lo mismo lleve dos alimentos o tres.
W_PREFERRED = 30.0
W_OFF_MEAL = 2_500.0

# Servir un plato sin su componente opcional (el almuerzo sin aguacate). Flojo a
# propósito: la versión completa es la buena y gana por defecto, pero cuando la
# alternativa es repetir un alimento el mismo día (W_SAME_DAY, 10.000) el motor
# prefiere quitar la grasa a poner el mismo aguacate dos veces —que era lo que
# reventaba el presupuesto de grasa del día—.
W_DROPPED = 60.0

# Pasarse del presupuesto de GRASA del día. Por gramo, y caro: un día que se pasa de
# grasa no se puede arreglar porcionando —los ítems de grasa ya están en su mínimo—,
# así que el reparador recorta la proteína para bajar las kcal y la comida se queda
# sin ella. Es una restricción, no una preferencia: 400 por gramo deja que un solo
# gramo de más pese más que cualquier consideración de variedad, pero se queda por
# debajo de W_SAME_DAY para no forzar un alimento repetido en el mismo día.
W_FAT_OVER = 400.0


def _affinity_cost(food: FoodItem, slot: MealSlot) -> float:
    weight = food.weight_in(slot)
    if weight < DEFAULT_SLOT_WEIGHT:
        return W_OFF_MEAL * (DEFAULT_SLOT_WEIGHT - weight)
    return -W_PREFERRED * (weight - DEFAULT_SLOT_WEIGHT)

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
        share = (
            {s: sh.protein_g for s, sh in config.meal_distribution.items()}
            if config
            else _FALLBACK_PROTEIN_SHARE
        )
        # Las comidas del cliente: las que su reparto declara, en el orden del día.
        self.slots = [s for s in SLOT_ORDER if s in share]
        targets = {slot: daily.protein_g * share[slot] for slot in self.slots}
        # El objetivo de carbohidrato manda el gramaje del carbo del plato, y ese
        # gramaje arrastra proteína (300 g de arroz son 8 g). Hace falta para saber
        # si un ancla de unidad entera aterriza (ver `dish_admissible`).
        carb_targets = (
            {s: daily.carb_g * sh.carb_g for s, sh in config.meal_distribution.items()}
            if config
            else {}
        )
        kcal_targets = (
            {s: daily.kcal * sh.kcal for s, sh in config.meal_distribution.items()}
            if config
            else {}
        )
        protein_tolerance = config.tolerances.protein_g if config else 0.10

        def admissible(food: FoodItem, slot: MealSlot) -> bool:
            return fits_protein(food, targets.get(slot, 0.0))

        def _fits_by_kcal(dish: Dish) -> bool:
            """¿Cabe este plato en las kcal del slot sin pasarse de proteína?

            Porcionar en gramos no basta para aterrizar donde sea: las kcal del
            slot fijan cuánto se sirve, y con ello la proteína. Un yogur griego
            en un snack de 129 kcal son ~22 g de proteína contra un objetivo de
            5 — no hay gramaje que lo arregle, porque bajarlo incumple las kcal.

            El límite inferior: cada alimento del plato pesa AL MENOS su porción
            mínima —el solver no baja de ahí— y las kcal que falten para llegar
            al objetivo se cubren, en el mejor de los casos, con el alimento
            menos proteico. Si ni con esa cuenta optimista se baja del objetivo,
            este plato no puede cuadrar en este slot nunca.
            """
            kcal_target = kcal_targets.get(dish.slot, 0.0)
            target = targets.get(dish.slot, 0.0)
            if kcal_target <= 0 or target <= 0:
                return True
            densidades = [
                f.protein_100g / f.kcal_100g for f in dish.foods if f.kcal_100g > 0
            ]
            if not densidades:
                return True

            proteina_base = kcal_base = 0.0
            for food in dish.foods:
                grams = food.portion_min_g or MIN_CO_PORTION_G
                proteina_base += food.protein_100g * grams / 100.0
                kcal_base += food.kcal_100g * grams / 100.0
            resto = max(kcal_target - kcal_base, 0.0)
            minima = proteina_base + resto * min(densidades)
            return minima - target <= max(target * protein_tolerance, MIN_RELEVANT_G)

        def dish_admissible(dish: Dish) -> bool:
            """¿Puede el ancla de este plato cuadrar la proteína del slot AQUÍ?

            Un alimento que va por unidades no se afina: la lata de atún son 25.5 g
            de proteína, o 51, o 76.5 — no hay puntos intermedios. Mirada SOLA, la
            lata "cabe" en un almuerzo de 42 g (dos latas son 51, dentro de
            tolerancia). Pero un plato no es su ancla: el arroz que lo acompaña pesa
            lo que haga falta para cubrir el carbohidrato del slot —unos 300 g— y eso
            son otros 5-7 g de proteína. El total se va a 57.6 g contra un objetivo de
            46.7, el día no cuadra, y el motor emitía ese plato igualmente: la
            generación moría cuatro intentos después sin decir de dónde venía el
            problema.

            Lo que el ancla tiene que cubrir es el objetivo MENOS lo que aportan sus
            acompañantes. El carbohidrato se estima por su propio objetivo (que es lo
            que va a decidir su gramaje); del resto basta su porción mínima.
            """
            anchor = dish.anchor
            if anchor.unit_granularity is UnitGranularity.GRAMS:
                return _fits_by_kcal(dish)
            target = targets.get(dish.slot, 0.0)
            unit_protein = anchor.protein_100g * (anchor.default_unit_g or 0.0) / 100.0
            if target <= 0 or unit_protein <= 0:
                return True  # su papel en el plato no es la proteína

            carb_target = carb_targets.get(dish.slot, 0.0)
            co_protein = 0.0
            for food in dish.foods:
                if food.id == anchor.id:
                    continue
                if food.category in CARB_GROUP and food.carb_100g > 0 and carb_target > 0:
                    # El carbo pesa lo que su objetivo mande, no su mínimo.
                    grams = carb_target / (food.carb_100g / 100.0)
                else:
                    grams = food.portion_min_g or MIN_CO_PORTION_G
                co_protein += food.protein_100g * grams / 100.0

            # El mejor número ENTERO (o medio) de unidades del ancla, y con él, lo
            # máximo que este plato puede acercarse al objetivo del slot.
            step = 1.0 if anchor.unit_granularity is UnitGranularity.WHOLE else 0.5
            count = max(
                round((target - co_protein) / unit_protein / step) * step, step
            )
            best_total = count * unit_protein + co_protein

            # Se juzga con la tolerancia RELATIVA del validador y sin su suelo de
            # ruido: aquí no estamos midiendo si un plan cuadra, sino decidiendo si
            # emitir un plato que quizá no pueda cuadrar nunca. El suelo de 10 g es
            # un umbral de RUIDO para comidas pequeñas; usarlo aquí ensancha a ±10 g
            # el margen de un almuerzo de 42 g y deja pasar justo lo que hay que
            # frenar — la lata de atún, que solo sabe dar 25.5 g o 51.
            return abs(best_total - target) <= target * protein_tolerance

        self.pools = expand(
            catalog, allowed, admissible=admissible, dish_admissible=dish_admissible
        )
        empty = [s.value for s in self.slots if not self.pools[s]]
        if empty:
            raise InsufficientDishes(
                "No hay ningún plato que se pueda cocinar con los alimentos de este "
                f"cliente en: {', '.join(empty)}."
            )
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
            id(dish): self._floor_fat(dish, targets, carb_targets)
            for pool in self.pools.values()
            for dish in pool
        }

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        raise LLMError(
            "La ingesta de documentos Word necesita el LLM real: configura "
            "ANTHROPIC_API_KEY en el .env (el modo offline solo genera planes)."
        )

    def pop_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "calls": self.calls}

    @staticmethod
    def _floor_fat(
        dish: Dish,
        protein_targets: dict[MealSlot, float],
        carb_targets: dict[MealSlot, float],
    ) -> float:
        """La grasa que este plato mete en el día SÍ O SÍ.

        No es la grasa que tendrá al final —eso lo decide el porcionador— sino la que
        arrastra por debajo: la carne pesa lo que su proteína mande, el arroz lo que
        mande su carbohidrato, y esa grasa entra en el día quiera o no.

        El ítem de grasa cuenta por su porción MÍNIMA, que es su suelo de verdad: el
        solver lo usa para cerrar el presupuesto y puede bajarlo, pero no a cero.
        Medio aguacate son 7 g de grasa, y tres platos con aguacate ya son 22 de los
        50 que tiene un día en déficit.
        """
        fat = 0.0
        for food in dish.foods:
            if food.category is FoodCategory.FAT:
                grams = food.portion_min_g or MIN_CO_PORTION_G
                fat += food.fat_100g * grams / 100.0
                continue
            if food.category in PROTEIN_GROUP and food.protein_100g > 0:
                target = protein_targets.get(dish.slot, 0.0)
                grams = target / (food.protein_100g / 100.0) if target else 0.0
            elif food.category in CARB_GROUP and food.carb_100g > 0:
                target = carb_targets.get(dish.slot, 0.0)
                grams = target / (food.carb_100g / 100.0) if target else 0.0
            else:
                grams = food.portion_min_g or MIN_CO_PORTION_G
            fat += food.fat_100g * grams / 100.0
        return fat

    def _cap_for(self, slot: MealSlot, category: FoodCategory, ndays: int) -> int:
        """El techo que `check_variety` va a aplicar a este alimento en este slot.

        Si el cliente tiene menos opciones que días, repetir es INEVITABLE y el
        techo se relaja: con dos carbos de desayuno y siete días, alguno sale
        cuatro veces y no es culpa del motor.
        """
        options = self._options.get((slot, category), 0) or 1
        return max(self._cap.get(category, 4), ceil(ndays / options))

    def select_week(self, *, seed: int, ndays: int = 7) -> list[dict[MealSlot, Dish]]:
        used_template: Counter[str] = Counter()
        used_food: Counter[str] = Counter()  # CRUZA SLOTS: aquí muere el yogur 14×
        used_in_slot: Counter[tuple[str, MealSlot]] = Counter()  # como cuenta el validador
        last_day: dict[str, int] = {}
        week: list[dict[MealSlot, Dish]] = []

        for day in range(ndays):
            today_foods: set[str] = set()
            today_anchors: set[str] = set()
            today_fat = 0.0  # la grasa que los platos de hoy ya arrastran
            chosen: dict[MealSlot, Dish] = {}

            for slot in self.slots:
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
                    # Lo que este plato haría al presupuesto de grasa del día. Solo
                    # se cobra lo que se PASA: mientras quepa, la grasa no opina.
                    over_fat = (
                        _fat() + self._dish_fat.get(id(dish), 0.0) - self._fat_budget
                    )
                    if over_fat > 0:
                        total += W_FAT_OVER * over_fat
                    total += W_TEMPLATE * used_template[dish.template_id] ** 2
                    if str(dish.anchor.id) in _anchors:
                        total += W_ANCHOR_DAY
                    for food in dish.foods:
                        # La afinidad se cobra a TODOS los alimentos, no sólo a los
                        # que cuentan para variedad: si alguien declara que un
                        # aceite es de cocina y no de desayuno, hay que hacerle caso.
                        total += _affinity_cost(food, _slot)
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
                today_anchors.add(str(best.anchor.id))
                for food in best.foods:
                    fid = str(food.id)
                    used_food[fid] += 1
                    used_in_slot[(fid, slot)] += 1
                    last_day[fid] = day
                    today_foods.add(fid)

            week.append(chosen)
        return week

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        seed = self._seed + self.calls  # el reintento desplaza el desempate
        self.calls += 1
        week = self.select_week(seed=seed)
        # El schema del LLM solo lleva food_ids —un modelo no conoce los ids de
        # plantilla—, así que la identidad del plato se guarda al lado y la lee
        # `_selection_to_days`. Sin esto, "Tostada de huevos con aguacate" se
        # perdía y quedaba la lista de alimentos.
        self.last_dishes = {
            (i, slot): dish
            for i, day in enumerate(week)
            for slot, dish in day.items()
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
