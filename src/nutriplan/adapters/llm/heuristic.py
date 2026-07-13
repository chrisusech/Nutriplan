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

from nutriplan.domain import meal_affinity
from nutriplan.domain.errors import LLMError
from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot, UnitGranularity
from nutriplan.domain.nutrition_config import NutritionConfig

T = TypeVar("T", bound=BaseModel)

# Reparto por defecto cuando no se pasa la config. Antes esto era una constante
# que DUPLICABA config.meal_distribution: cambiar el YAML desincronizaba el
# selector en silencio. Ahora la config manda y esto es solo el último recurso.
_FALLBACK_SHARE = {
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

    def __init__(
        self,
        allowed: list[FoodItem],
        daily_protein_g: float = 120.0,
        seed: int = 0,
        config: NutritionConfig | None = None,
    ) -> None:
        # `seed` desplaza la rotación: dos versiones del plan del mismo cliente
        # (mes 1 vs mes 2) arrancan en combinaciones distintas → menús diferentes.
        self._seed = seed
        share = dict(config.meal_distribution) if config else _FALLBACK_SHARE
        def by_cat(c: FoodCategory) -> list[FoodItem]:
            return sorted((f for f in allowed if f.category == c), key=lambda f: f.name_es)

        def dense(items: list[FoodItem], attr: str, minimum: float,
                  min_count: int = 1) -> list[FoodItem]:
            """El motor solo usa fuentes densas: una legumbre como 'proteína' o
            un carbo flojo no cuadran objetivos altos (tope de 600 g/porción).
            Se relaja si dejaría menos de min_count opciones."""
            filtered = [f for f in items if getattr(f, attr) >= minimum]
            return filtered if len(filtered) >= min_count else items

        all_proteins = by_cat(FoodCategory.PROTEIN)
        # Fuentes densas para platos principales (una clara de huevo o una legumbre
        # no cuadran un objetivo alto con el tope de 600 g/porción).
        proteins = dense(all_proteins, "protein_100g", 12.0)
        dairy = dense(by_cat(FoodCategory.DAIRY), "protein_100g", 8.0)
        # Los huevos se buscan en TODA la categoría: la clara (10.9 g) no pasa el
        # filtro denso pero sigue siendo un desayuno/snack legítimo.
        eggs = [f for f in all_proteins if meal_affinity.is_egg(f)]
        shakes = [f for f in all_proteins if meal_affinity.is_shake(f)]
        light_dairy = [f for f in dairy if f.fat_100g <= 5.0] or dairy

        # Platos principales (almuerzo/cena): carnes, pescado, mariscos, tofu,
        # legumbres — lo salado. Magras de preferencia (la grasa del día vive en
        # los ítems de grasa) y que cuadren el objetivo del almuerzo.
        lunch_pt = daily_protein_g * share[MealSlot.LUNCH]
        savory = [f for f in proteins if meal_affinity.main_protein(f)] or proteins
        lean = [f for f in savory if f.fat_100g <= 0.4 * f.protein_100g] or savory
        self.main_proteins = [f for f in lean if _fits_protein(f, lunch_pt)] or lean

        # Snacks: ligeros y variados. Lácteos, huevos/claras, lonchas y batido;
        # nunca carne de plato principal. Se intercalan categorías para no repetir
        # yogur 14× en la semana.
        snack_pt = daily_protein_g * share[MealSlot.SNACK_AM]
        snack_eligible = [
            f for f in allowed
            if meal_affinity.snack_protein(f) and _fits_protein(f, snack_pt)
        ]
        snack_dairy = sorted(
            (f for f in snack_eligible if f.category is FoodCategory.DAIRY),
            key=lambda f: f.name_es,
        )
        snack_eggs = sorted(
            (f for f in snack_eligible if meal_affinity.is_egg(f)),
            key=lambda f: f.name_es,
        )
        snack_slices = sorted(
            (
                f for f in snack_eligible
                if f.category is FoodCategory.PROTEIN and not meal_affinity.is_egg(f)
            ),
            key=lambda f: f.name_es,
        )
        snack_shakes = sorted(
            (f for f in snack_eligible if meal_affinity.is_shake(f)),
            key=lambda f: f.name_es,
        )

        def _interleave(*pools: list[FoodItem]) -> list[FoodItem]:
            active = [p for p in pools if p]
            if not active:
                return []
            out: list[FoodItem] = []
            for i in range(max(len(p) for p in active)):
                for pool in active:
                    if i < len(pool):
                        out.append(pool[i])
            return out

        self.snack_pool = _interleave(snack_dairy, snack_eggs, snack_slices, snack_shakes)
        if not self.snack_pool:
            fallback = light_dairy or eggs or shakes or proteins
            self.snack_pool = [f for f in fallback if _fits_protein(f, snack_pt)] or fallback
        self.snack_proteins = self.snack_pool  # alias legado para tests que lo lean

        # Desayuno: los HUEVOS son el ancla (aparecen la mayoría de días); lácteos
        # y batido rotan como variación. Nunca carne/pescado al desayuno.
        bfast_pt = daily_protein_g * share[MealSlot.BREAKFAST]
        self.egg_anchor = max(eggs, key=lambda f: f.fat_100g) if eggs else None  # huevo entero
        bfast_alts = light_dairy + shakes
        self.breakfast_alts = [f for f in bfast_alts if _fits_protein(f, bfast_pt)] or bfast_alts
        # Último recurso si el cliente no tiene huevos ni lácteos: algo que cuadre.
        self.breakfast_fallback = [f for f in proteins if _fits_protein(f, bfast_pt)] or proteins

        all_carbs = dense(by_cat(FoodCategory.CARB), "carb_100g", 20.0)
        # El solver reparte por ROLES: cada alimento cubre su macro dominante y
        # los cruces se compensan por punto fijo. Una legumbre rompe la premisa
        # — es carbo Y proteína a la vez. Con 240 g de frijoles al almuerzo y
        # 180 g de garbanzos a la cena entran 37 g de proteína en un día que
        # pide 99, y ya no hay forma de cuadrar: o el desayuno se queda sin
        # huevos o el día se pasa de proteína. Siguen en el catálogo (el
        # entrenador puede ponerlas a mano y la IA elegirlas), pero el motor
        # determinista no las usa de carbohidrato principal salvo que no haya
        # nada más.
        carb_led = [f for f in all_carbs if f.protein_100g <= 0.3 * f.carb_100g]
        self.breakfast_carbs = [f for f in all_carbs if meal_affinity.is_breakfast_carb(f)] \
            or all_carbs
        self.main_carbs = [f for f in carb_led if meal_affinity.is_main_carb(f)] \
            or [f for f in all_carbs if meal_affinity.is_main_carb(f)] or all_carbs
        self.fruits = by_cat(FoodCategory.FRUIT)
        # Grasas muy proteicas (maní, almendras) desbalancean el desayuno.
        fats = [f for f in by_cat(FoodCategory.FAT) if f.protein_100g <= 10.0] \
            or by_cat(FoodCategory.FAT)
        # Por slot: nadie se toma dos cucharadas de aceite de oliva en el desayuno.
        # La afinidad la declara el alimento (`meal_slots`), no una lista aquí.
        self.fats_by_slot = {
            slot: [f for f in fats if meal_affinity.allows(f, slot)] or fats
            for slot in MealSlot
        }
        self.calls = 0

    async def extract(self, *, system: str, text: str, schema: type[T], model: str) -> T:
        raise LLMError(
            "La ingesta de documentos Word necesita el LLM real: configura "
            "ANTHROPIC_API_KEY en el .env (el modo offline solo genera planes)."
        )

    def pop_usage(self) -> dict[str, int]:
        return {"input_tokens": 0, "output_tokens": 0, "calls": self.calls}

    async def select_plan(self, *, system: str, prompt: str, schema: type[T], model: str) -> T:
        offset = self._seed + self.calls  # seed = versión; calls = reintento
        self.calls += 1

        Pm, Ps = self.main_proteins, self.snack_pool
        Cb, Cm, F = self.breakfast_carbs, self.main_carbs, self.fruits
        breakfast_ok = self.egg_anchor is not None or self.breakfast_alts
        if not Pm or not Cb or not Cm or not F or not Ps or not breakfast_ok:
            raise LLMError(
                "El conjunto permitido no tiene fuentes suficientes por slot "
                "(se requieren proteínas magras, carbohidratos y frutas)."
            )

        def pick(pool: list[FoodItem], i: int, shift: int = 0) -> FoodItem:
            """Rotación por día: recorre el pool para no repetir el alimento."""
            return pool[(i + shift + offset) % len(pool)]

        def fat_for(slot: MealSlot, i: int, shift: int = 0) -> list[str]:
            """Un ítem de grasa en cada comida principal.

            La grasa del día se reparte entre los ítems de grasa (portioning.py la
            cierra a nivel de día). Con uno solo, ese uno se la come TODA: el
            desayuno acababa con 190 g de aguacate. Los planes reales llevan grasa
            en desayuno, almuerzo y cena — así a cada uno le toca ~un tercio.
            """
            pool = self.fats_by_slot[slot]
            return [str(pick(pool, i, shift).id)] if pool else []

        def breakfast_protein(i: int) -> FoodItem:
            """Huevos la mayoría de días (~4/7); el resto rota a lácteo/batido."""
            egg_days = 4  # cuadra con max_protein_repeats_per_week (≤4 en desayuno)
            variety_day = self.breakfast_alts and (i + offset) % 7 >= egg_days
            if self.egg_anchor is not None and not variety_day:
                return self.egg_anchor
            if self.breakfast_alts:
                return pick(self.breakfast_alts, i)
            if self.egg_anchor is not None:
                return self.egg_anchor
            return pick(self.breakfast_fallback, i)

        def snack_protein_for(day_i: int, slot: MealSlot) -> FoodItem:
            """AM y PM rotan en un pool intercalado; evita el mismo ítem dos veces al día.

            El paso de rotación es 1, NO 2. Con paso 2 y un pool de tamaño par,
            gcd(2, len) = 2 y el índice `(2·día + offset) % len` es CONSTANTE toda
            la semana: un cliente con dos snacks posibles comía el mismo yogur los
            7 días. Con paso 1 el recorrido es un ciclo completo del pool.
            """
            pool = [f for f in Ps if meal_affinity.allows(f, slot)] or Ps
            choice = pick(pool, day_i)
            if slot is MealSlot.SNACK_PM and len(pool) > 1:
                am_pool = [f for f in Ps if meal_affinity.allows(f, MealSlot.SNACK_AM)] or Ps
                am = pick(am_pool, day_i)
                if choice.id == am.id:
                    choice = pick(pool, day_i + 1)
            return choice

        days = []
        for i in range(7):
            meals = [
                {"slot": MealSlot.BREAKFAST.value,
                 "food_ids": [str(breakfast_protein(i).id), str(pick(Cb, i).id)]
                 + fat_for(MealSlot.BREAKFAST, i)},
                {"slot": MealSlot.SNACK_AM.value,
                 "food_ids": [str(snack_protein_for(i, MealSlot.SNACK_AM).id),
                              str(pick(F, i).id)]},
                {"slot": MealSlot.LUNCH.value,
                 "food_ids": [str(pick(Pm, i).id), str(pick(Cm, i, shift=1).id)]
                 + fat_for(MealSlot.LUNCH, i),
                 "free_salad": True},
                {"slot": MealSlot.SNACK_PM.value,
                 "food_ids": [str(snack_protein_for(i, MealSlot.SNACK_PM).id),
                              str(pick(F, i, shift=1).id)]},
                {"slot": MealSlot.DINNER.value,
                 "food_ids": [str(pick(Pm, i, shift=1).id), str(pick(Cm, i, shift=2).id)]
                 + fat_for(MealSlot.DINNER, i, shift=1),
                 "free_salad": True},
            ]
            days.append({"day_index": i, "meals": meals})
        return schema.model_validate({"days": days})
