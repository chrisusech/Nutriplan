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
from nutriplan.domain.models import (
    DEFAULT_SLOT_WEIGHT,
    FoodCategory,
    FoodItem,
    MealSlot,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.portioning import can_cover_carb, fits_protein

T = TypeVar("T", bound=BaseModel)

# Peso de cada comida en la PROTEÍNA del día, cuando no se pasa la config. Antes
# esto era una constante que DUPLICABA config.meal_distribution: cambiar el YAML
# desincronizaba el selector en silencio. Ahora la config manda y esto es solo el
# último recurso.
_FALLBACK_PROTEIN_SHARE = {
    MealSlot.BREAKFAST: 0.22,
    MealSlot.SNACK_AM: 0.05,
    MealSlot.LUNCH: 0.36,
    MealSlot.SNACK_PM: 0.05,
    MealSlot.DINNER: 0.32,
}


def _unit_protein(food: FoodItem) -> float:
    """Proteína por unidad servible (huevo, loncha, lata). En gramos si no hay unidad."""
    return food.protein_100g * (food.default_unit_g or 100.0) / 100.0


# `fits_protein` vive en el dominio (portioning): la usan este motor y el de
# platos, y estaba duplicada.
_fits_protein = fits_protein


class HeuristicSelector:
    """Implementa el puerto LLMClient (solo select_plan). Motor principal offline."""

    def __init__(
        self,
        allowed: list[FoodItem],
        daily_protein_g: float = 120.0,
        seed: int = 0,
        config: NutritionConfig | None = None,
        daily_carb_g: float = 200.0,
    ) -> None:
        # `seed` desplaza la rotación: dos versiones del plan del mismo cliente
        # (mes 1 vs mes 2) arrancan en combinaciones distintas → menús diferentes.
        self._seed = seed
        share = (
            {s: sh.protein_g for s, sh in config.meal_distribution.items()}
            if config
            else _FALLBACK_PROTEIN_SHARE
        )
        carb_share = (
            {s: sh.carb_g for s, sh in config.meal_distribution.items()}
            if config
            else {
                MealSlot.BREAKFAST: 0.30,
                MealSlot.SNACK_AM: 0.10,
                MealSlot.LUNCH: 0.30,
                MealSlot.SNACK_PM: 0.10,
                MealSlot.DINNER: 0.20,
            }
        )
        # Las comidas de este cliente, en el orden del día.
        self.slots = [s for s in MealSlot if s in share]

        def by_cat(c: FoodCategory) -> list[FoodItem]:
            return sorted((f for f in allowed if f.category == c), key=lambda f: f.name_es)

        def dense(
            items: list[FoodItem], attr: str, minimum: float, min_count: int = 1
        ) -> list[FoodItem]:
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
        # los ítems de grasa).
        savory = [f for f in proteins if meal_affinity.main_protein(f)] or proteins
        lean = [f for f in savory if f.fat_100g <= 0.4 * f.protein_100g] or savory
        # El pool se filtra CONTRA EL OBJETIVO DE CADA SLOT, no solo contra el del
        # almuerzo. La cena pide menos proteína (0.32 del día frente a 0.36), y una
        # lata entera de atún —que no se porciona: su rejilla es de 100 g— cabe en
        # el almuerzo pero se pasa 10 g en la cena, y el solver no puede bajarla.
        self.main_proteins_by_slot = {
            slot: [f for f in lean if _fits_protein(f, daily_protein_g * share.get(slot, 0.0))]
            or lean
            for slot in (MealSlot.LUNCH, MealSlot.DINNER)
        }
        self.main_proteins = self.main_proteins_by_slot[MealSlot.LUNCH]  # alias legado

        # Snacks: saciedad, no una comida en pequeño. Una fruta, una fruta con
        # crema de frutos secos, un yogur griego con fruta. El HUEVO ya no entra —
        # y no por una lista negra aquí, sino porque el catálogo dejó de declararle
        # los slots de snack (`meal_affinity.allows`).
        snack_pt = daily_protein_g * share.get(MealSlot.SNACK_AM, 0.05)
        snack_eligible = [
            f for f in allowed if meal_affinity.snack_protein(f) and _fits_protein(f, snack_pt)
        ]
        # El lácteo del snack va MAGRO, igual que el del desayuno. Un cheddar
        # (34 g de grasa/100 g) mete 10 g de grasa escondida en un snack que apenas
        # vale el 7.5% del día: el presupuesto de grasa se agota antes de llegar a
        # los ítems de grasa, que ya no pueden bajar de su porción mínima, y el
        # reparador acaba recortando el HUEVO DEL DESAYUNO para compensar —
        # dejándolo sin proteína. La grasa del día vive en los ítems de grasa,
        # no escondida en el queso del media mañana.
        snack_dairy_all = [f for f in snack_eligible if f.category is FoodCategory.DAIRY]
        snack_dairy = sorted(
            [f for f in snack_dairy_all if f.fat_100g <= 5.0] or snack_dairy_all,
            key=lambda f: f.name_es,
        )
        snack_rest = sorted(
            (f for f in snack_eligible if f.category is not FoodCategory.DAIRY),
            key=lambda f: f.name_es,
        )
        self.snack_pool = snack_dairy + snack_rest
        self.snack_proteins = self.snack_pool  # alias legado para tests que lo lean

        # Desayuno: los HUEVOS son el ancla (aparecen la mayoría de días); lácteos
        # y batido rotan como variación. Nunca carne/pescado al desayuno.
        bfast_pt = daily_protein_g * share.get(MealSlot.BREAKFAST, 0.22)
        # El ancla es el huevo que más proteína aporta POR UNIDAD, para que una
        # ración normal (2-3 unidades) cubra el slot. Antes se elegía el huevo con
        # más GRASA — un atajo para preferir el entero sobre la clara que, al
        # crecer el catálogo, coronó al huevo de codorniz (11.1 g de grasa pero
        # 1.3 g de proteína por unidad de 10 g): el solver pedía 14 huevos de
        # codorniz al desayuno.
        self.egg_anchor = max(eggs, key=_unit_protein) if eggs else None
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

        # La BASE de una comida es un alimento que pertenece a esa comida: el arroz y
        # la papa al almuerzo, la avena y el pan al desayuno. Lo que solo "cabe"
        # (`weight_in` por debajo del defecto: la arepa a mediodía, la granola de
        # desayuno) queda fuera del pool mientras haya algo mejor, y vuelve —con el
        # `or`— cuando el cliente no tiene otra cosa: quien solo come arepa recibe
        # arepa, que es preferible a no recibir plan.
        #
        # No es cosmética. La granola lleva 13 g de grasa por 100, y de base de un
        # desayuno tiene que aportar unos 64 g de carbohidrato: se come sola el
        # presupuesto de grasa de un día en déficit y el día entero deja de cuadrar.
        # Ordenar es estable a propósito: a igual peso mandan el catálogo y la lista
        # del cliente, que es la rotación que este motor ya usaba.
        def base_for(foods: list[FoodItem], slot: MealSlot) -> list[FoodItem]:
            belongs = [f for f in foods if f.weight_in(slot) >= DEFAULT_SLOT_WEIGHT]
            return sorted(belongs or foods, key=lambda f: -f.weight_in(slot))

        # Sin `can_cover_carb`, el pan blanco (tope 90 g) entra al pool del
        # desayuno y el solver se queda corto de carbo (~49 g vs ~64). Sin comida
        # libre a veces cae dentro de la tolerancia; con ella, el cierre de grasa
        # del día de 4 comidas lo empuja fuera y tumba la semana entera.
        def coverable(foods: list[FoodItem], slot: MealSlot) -> list[FoodItem]:
            target = daily_carb_g * carb_share.get(slot, 0.0)
            ok = [f for f in foods if can_cover_carb(f, target)]
            return ok or foods

        self.breakfast_carbs = coverable(
            base_for(
                [f for f in all_carbs if meal_affinity.is_breakfast_carb(f)] or all_carbs,
                MealSlot.BREAKFAST,
            ),
            MealSlot.BREAKFAST,
        )
        self.main_carbs = coverable(
            base_for(
                [f for f in carb_led if meal_affinity.is_main_carb(f)]
                or [f for f in all_carbs if meal_affinity.is_main_carb(f)]
                or all_carbs,
                MealSlot.LUNCH,
            ),
            MealSlot.LUNCH,
        )
        self.fruits = by_cat(FoodCategory.FRUIT)
        # Grasas muy proteicas (maní, almendras) desbalancean el desayuno.
        fats = [f for f in by_cat(FoodCategory.FAT) if f.protein_100g <= 10.0] or by_cat(
            FoodCategory.FAT
        )
        # Por slot: nadie se toma dos cucharadas de aceite de oliva en el desayuno.
        # La afinidad la declara el alimento (`meal_slots`), no una lista aquí.
        self.fats_by_slot = {
            slot: [f for f in fats if meal_affinity.allows(f, slot)] or fats for slot in MealSlot
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

        Pl = self.main_proteins_by_slot[MealSlot.LUNCH]
        Pd = self.main_proteins_by_slot[MealSlot.DINNER]
        Ps = self.snack_pool
        Cb, Cm, F = self.breakfast_carbs, self.main_carbs, self.fruits
        breakfast_ok = self.egg_anchor is not None or self.breakfast_alts
        # El snack ya no exige proteína: le basta una fruta (o, si no hay ninguna,
        # un lácteo). Lo que no puede es quedarse VACÍO, y hay que comprobarlo slot
        # a slot: una fruta que solo va a media mañana no salva el snack de la
        # tarde, y una comida sin alimentos no la acepta ni el esquema.
        snack_ok = all(
            any(meal_affinity.allows(f, slot) for f in [*F, *Ps])
            for slot in self.slots
            if slot in (MealSlot.SNACK_AM, MealSlot.SNACK_PM)
        )
        if not Pl or not Pd or not Cb or not Cm or not snack_ok or not breakfast_ok:
            raise LLMError(
                "El conjunto permitido no tiene fuentes suficientes por slot "
                "(se requieren proteínas magras, carbohidratos y fruta o lácteo)."
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

        def snack_for(day_i: int, slot: MealSlot, shift: int = 0) -> list[str]:
            """El snack del día: fruta, fruta + grasa, o lácteo + fruta.

            Rota entre las tres formas para que la semana no sea siete yogures. Sin
            fruta en la lista del cliente, el snack es el lácteo solo; sin lácteo ni
            grasa, la fruta sola — que es un snack perfectamente digno.

            La rotación va de uno en uno, NO de dos en dos. Con paso 2 y un pool de
            tamaño par, gcd(2, len) = 2 y el índice `(2·día + offset) % len` es
            CONSTANTE toda la semana: ahí vivía el yogur de los siete días.
            """
            fruits = [f for f in F if meal_affinity.allows(f, slot)]
            dairy = [f for f in Ps if meal_affinity.allows(f, slot)]
            fats = [f for f in self.fats_by_slot[slot] if meal_affinity.allows(f, slot)]
            if not fruits:
                return [str(pick(dairy, day_i, shift).id)] if dairy else []

            fruit = str(pick(fruits, day_i, shift).id)
            forms = [pool for pool in (dairy, fats) if pool]
            if not forms:
                return [fruit]
            # La tercera forma es la fruta sola: un banano a media mañana no
            # necesita compañía, y los macros los cierran las comidas grandes.
            form = (day_i + shift + offset) % (len(forms) + 1)
            if form == len(forms):
                return [fruit]
            return [str(pick(forms[form], day_i, shift).id), fruit]

        def meal_for(slot: MealSlot, i: int) -> dict[str, object]:
            """Cada comida del día. Solo se arman las que el cliente come."""
            if slot is MealSlot.BREAKFAST:
                return {
                    "slot": slot.value,
                    "food_ids": [str(breakfast_protein(i).id), str(pick(Cb, i).id)]
                    + fat_for(slot, i),
                }
            if slot is MealSlot.LUNCH:
                return {
                    "slot": slot.value,
                    "food_ids": [str(pick(Pl, i).id), str(pick(Cm, i, shift=1).id)]
                    + fat_for(slot, i),
                    "free_salad": True,
                }
            if slot is MealSlot.DINNER:
                return {
                    "slot": slot.value,
                    "food_ids": [str(pick(Pd, i, shift=1).id), str(pick(Cm, i, shift=2).id)]
                    + fat_for(slot, i, shift=1),
                    "free_salad": True,
                }
            shift = 1 if slot is MealSlot.SNACK_PM else 0
            return {"slot": slot.value, "food_ids": snack_for(i, slot, shift=shift)}

        days = [
            {"day_index": i, "meals": [meal_for(slot, i) for slot in self.slots]} for i in range(7)
        ]
        return schema.model_validate({"days": days})
