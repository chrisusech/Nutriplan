"""Qué platos puede cocinar esta persona, antes de elegir ninguno.

Todo lo de aquí responde a una sola pregunta: ¿este plato PUEDE cuadrar? No cuál
conviene —eso es coste, y vive en `template_costs`— sino cuál es capaz de
aterrizar en los gramos del slot sin porciones absurdas. Separarlo del selector
es lo que deja leer el filtro sin el algoritmo encima: son las reglas de la
cocina, no las de la elección.

El orden importa. Emitir un plato que nunca podrá cuadrar no se nota aquí: se
nota cuatro intentos después, cuando la semana entera falla con un «objetivo
128.5, real 87.3» que no dice de dónde viene.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from nutriplan.adapters.llm.template_costs import FALLBACK_PROTEIN_SHARE, MIN_CO_PORTION_G
from nutriplan.domain.errors import LLMError
from nutriplan.domain.generation_rules import CARB_GROUP, PROTEIN_GROUP
from nutriplan.domain.meal_template import Dish, MealCatalog, expand
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealSlot,
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import can_cover_carb, fits_protein, max_carb_g
from nutriplan.domain.validation import MIN_RELEVANT_G

DEFAULT_PROTEIN_TOLERANCE = 0.10


class InsufficientDishes(LLMError):
    """La lista del cliente no da para armar ningún plato en algún slot."""


@dataclass(frozen=True)
class SlotTargets:
    """Lo que cada comida tiene que dar. Es la vara con la que se mide un plato."""

    protein: dict[MealSlot, float]
    carb: dict[MealSlot, float]
    kcal: dict[MealSlot, float]
    protein_tolerance: float

    @classmethod
    def build(cls, daily: MacroTargets, config: NutritionConfig | None) -> SlotTargets:
        share = (
            {s: sh.protein_g for s, sh in config.meal_distribution.items()}
            if config
            else FALLBACK_PROTEIN_SHARE
        )
        return cls(
            protein={slot: daily.protein_g * sh for slot, sh in share.items()},
            # El objetivo de carbohidrato manda el gramaje del carbo del plato, y ese
            # gramaje arrastra proteína (300 g de arroz son 8 g). Hace falta para saber
            # si un ancla de unidad entera aterriza.
            carb=(
                {s: daily.carb_g * sh.carb_g for s, sh in config.meal_distribution.items()}
                if config
                else {}
            ),
            kcal=(
                {s: daily.kcal * sh.kcal for s, sh in config.meal_distribution.items()}
                if config
                else {}
            ),
            protein_tolerance=config.tolerances.protein_g if config else DEFAULT_PROTEIN_TOLERANCE,
        )


class DishFilter:
    """Decide si un plato puede cuadrar. Sin estado del día: solo aritmética."""

    def __init__(self, targets: SlotTargets) -> None:
        self._t = targets

    def food_ok(self, food: FoodItem, slot: MealSlot) -> bool:
        return fits_protein(food, self._t.protein.get(slot, 0.0))

    def fits_by_kcal(self, dish: Dish) -> bool:
        """¿Cabe este plato en las kcal del slot sin pasarse de proteína?

        Porcionar en gramos no basta para aterrizar donde sea: las kcal del
        slot fijan cuánto se sirve, y con ello la proteína. Un yogur griego
        en un snack de 129 kcal son ~22 g de proteína contra un objetivo de
        5 — no hay gramaje que lo arregle, porque bajarlo incumple las kcal.

        Dos suelos, se toma el peor (el más alto):
        A) Optimista por kcal: cada alimento en su porción mínima y el resto
           de kcal con el alimento menos proteico. Caza el snack de yogur.
        B) Como el solver: los carbos pesan lo que mande su objetivo del
           slot (pan/avena arrastran proteína). Sin esto, un desayuno
           lácteo+pan pasaba el filtro A (~33 g vs 26) y fallaba al
           aterrizar (~41 g).
        """
        kcal_target = self._t.kcal.get(dish.slot, 0.0)
        carb_target = self._t.carb.get(dish.slot, 0.0)
        target = self._t.protein.get(dish.slot, 0.0)
        if kcal_target <= 0 or target <= 0:
            return True
        densidades = [f.protein_100g / f.kcal_100g for f in dish.foods if f.kcal_100g > 0]
        if not densidades:
            return True

        proteina_base = kcal_base = 0.0
        minima_solver = 0.0
        for food in dish.foods:
            floor_g = food.portion_min_g or MIN_CO_PORTION_G
            proteina_base += food.protein_100g * floor_g / 100.0
            kcal_base += food.kcal_100g * floor_g / 100.0
            if food.category in CARB_GROUP and food.carb_100g > 0 and carb_target > 0:
                grams = carb_target / (food.carb_100g / 100.0)
            else:
                grams = floor_g
            minima_solver += food.protein_100g * grams / 100.0
        resto = max(kcal_target - kcal_base, 0.0)
        minima_kcal = proteina_base + resto * min(densidades)
        minima = max(minima_kcal, minima_solver)
        # El filtro tiene que ser MÁS estrecho que el validador: el solver
        # aterriza por encima del mínimo. Sin este margen de 2 g, un plato
        # con suelo 48.4 g pasaba el ±10 g y fallaba en 48.6 al validar.
        allowance = max(target * self._t.protein_tolerance, MIN_RELEVANT_G) - 2.0
        return minima - target <= max(allowance, 0.0)

    def carb_falls_short(self, dish: Dish) -> bool:
        """¿Este plato se queda corto de carbohidrato haga lo que haga el solver?

        Se suman los TOPES de todas sus fuentes de carbo —la fruta también, que
        para el porcionador es una de ellas—: eso es lo máximo que el plato
        puede dar. Un plátano verde topeado en 300 g da 87 g de carbo, y un
        almuerzo que pide 128 no lo cuadra nunca; en cambio un banano con avena
        sí llega entre los dos, y ese plato hay que dejarlo pasar.

        Esto solo se cobraba como coste (`W_UNDER_CARB`), así que cuando repetir
        el arroz salía más caro que quedarse corto, el motor servía el plátano:
        el día no validaba y la semana moría con "objetivo 128.5, real 87.3"
        tras seis intentos.
        """
        carb_target = self._t.carb.get(dish.slot, 0.0)
        techo = sum(
            max_carb_g(f) for f in dish.foods if f.category in CARB_GROUP and f.carb_100g > 0
        )
        return carb_target > 0 and techo > 0 and techo < carb_target

    def admissible(self, dish: Dish) -> bool:
        """¿Puede este plato cuadrar proteína Y carbohidratos sin porciones absurdas?

        Un alimento que va por unidades no se afina: la lata de atún son 25.5 g
        de proteína, o 51, o 76.5 — no hay puntos intermedios. Mirada SOLA, la
        lata "cabe" en un almuerzo de 42 g (dos latas son 51, dentro de
        tolerancia). Pero un plato no es su ancla: el arroz que lo acompaña pesa
        lo que haga falta para cubrir el carbohidrato del slot —unos 300 g— y eso
        son otros 5-7 g de proteína. El total se va a 57.6 g contra un objetivo de
        46.7, el día no cuadra, y el motor emitía ese plato igualmente: la
        generación moría cuatro intentos después sin decir de dónde venía el
        problema.

        Igual de crítico: si el único carbo es tortilla y el slot pide 130 g de
        carbo, harían falta ~9 tortillas. Con `portion_max_g` el solver no las
        sirve… y el día falla. Mejor no ofrecer ese plato.

        Que el plato llegue al carbohidrato del slot lo mira `carb_falls_short`
        aparte, porque esa exigencia sabe relajarse cuando ningún plato la
        cumple.
        """
        carb_target = self._t.carb.get(dish.slot, 0.0)
        # Contables (tortilla/arepa/pan): no emitir platos de UNA sola
        # arepa que necesitarían 9 unidades. Si hay otro carbo (fruta,
        # avena), el techo conjunto decide: el solver reparte y respeta
        # portion_max_g. Sin esto, huevos+arepa salían del pool en un
        # desayuno de ~130 g y solo quedaban bowls de avena.
        tiene_ayuda = any(
            f.category in CARB_GROUP
            and f.carb_100g > 0
            and not (
                f.category is FoodCategory.CARB and f.unit_granularity is not UnitGranularity.GRAMS
            )
            for f in dish.foods
        )
        if not tiene_ayuda:
            for food in dish.foods:
                if (
                    food.category is FoodCategory.CARB
                    and food.unit_granularity is not UnitGranularity.GRAMS
                    and not can_cover_carb(food, carb_target)
                ):
                    return False

        anchor = dish.anchor
        if anchor.unit_granularity is UnitGranularity.GRAMS:
            return self.fits_by_kcal(dish)
        target = self._t.protein.get(dish.slot, 0.0)
        unit_protein = anchor.protein_100g * (anchor.default_unit_g or 0.0) / 100.0
        if target <= 0 or unit_protein <= 0:
            return True  # su papel en el plato no es la proteína

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
        count = max(round((target - co_protein) / unit_protein / step) * step, step)
        best_total = count * unit_protein + co_protein

        # Se juzga con la tolerancia RELATIVA del validador y sin su suelo de
        # ruido: aquí no estamos midiendo si un plan cuadra, sino decidiendo si
        # emitir un plato que quizá no pueda cuadrar nunca. El suelo de 10 g es
        # un umbral de RUIDO para comidas pequeñas; usarlo aquí ensancha a ±10 g
        # el margen de un almuerzo de 42 g y deja pasar justo lo que hay que
        # frenar — la lata de atún, que solo sabe dar 25.5 g o 51.
        return abs(best_total - target) <= target * self._t.protein_tolerance

    def floor_fat(self, dish: Dish) -> float:
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
                target = self._t.protein.get(dish.slot, 0.0)
                grams = target / (food.protein_100g / 100.0) if target else 0.0
            elif food.category in CARB_GROUP and food.carb_100g > 0:
                target = self._t.carb.get(dish.slot, 0.0)
                grams = target / (food.carb_100g / 100.0) if target else 0.0
            else:
                grams = food.portion_min_g or MIN_CO_PORTION_G
            fat += food.fat_100g * grams / 100.0
        return fat


def build_pools(
    *,
    catalog: MealCatalog,
    allowed: list[FoodItem],
    filt: DishFilter,
    slots: list[MealSlot],
    on_hand_ids: frozenset[UUID] = frozenset(),
) -> dict[MealSlot, list[Dish]]:
    """Los platos cocinables de cada comida. Falla si alguna se queda sin ninguno."""

    def pools_for(*, carbo_completo: bool) -> dict[MealSlot, list[Dish]]:
        def admisible(dish: Dish) -> bool:
            if carbo_completo and filt.carb_falls_short(dish):
                return False
            return filt.admissible(dish)

        return expand(
            catalog,
            allowed,
            admissible=filt.food_ok,
            dish_admissible=admisible,
            on_hand=on_hand_ids,
        )

    pools = pools_for(carbo_completo=True)
    # Hay slots donde el objetivo de carbo no lo alcanza NINGÚN alimento del
    # catálogo (un desayuno de 153 g pide más avena de la que se sirve). Ahí
    # `expand` se rinde y suelta todo lo que había rechazado —incluidos los
    # platos de nueve tortillas—, así que ese slot vuelve al pool de siempre,
    # que sí frena los contables imposibles. El coste `W_UNDER_CARB` sigue
    # ordenando lo que queda de menos a más corto.
    lax: dict[MealSlot, list[Dish]] | None = None
    for slot, pool in list(pools.items()):
        if pool and not any(filt.carb_falls_short(d) for d in pool):
            continue
        if lax is None:
            lax = pools_for(carbo_completo=False)
        pools[slot] = lax[slot]
    empty = [s.value for s in slots if not pools[s]]
    if empty:
        raise InsufficientDishes(
            "No hay ningún plato que se pueda cocinar con los alimentos de este "
            f"cliente en: {', '.join(empty)}."
        )
    return pools
