"""Portion solver por roles (sección 11.3) — determinista, sin IA.

Estrategia:
1. Proteína y carbohidratos se resuelven POR SLOT contra el reparto de
   `macro_split.macro_shares` (cada alimento cubre su macro dominante; se
   ajustan los aportes cruzados por punto fijo). El reparto va SOLO a las
   comidas que tienen fuente de ese macro: un snack de solo fruta no lleva
   proteína, y la suya la absorben las comidas grandes.
2. La grasa cierra A NIVEL DE DÍA: los ítems de grasa explícitos absorben la
   grasa faltante tras contar la que ya traen proteínas y carbos. (Un snack
   de yogur+fruta no puede aportar la grasa de su % de kcal; esa cuota vive
   donde hay fuente real.)
3. Cada porción aterriza en la REJILLA de su alimento: el huevo salta de 50 en
   50, el arroz de 10 en 10. Nadie pesa 137 g.
4. El residuo del redondeo se repara con una búsqueda local entera. Los pasos 1
   y 2 son continuos y la rejilla no lo es: un huevo de más son 143 kcal, y ese
   error no lo absorbe nadie por su cuenta.

El código es dueño de los números: `computed` SIEMPRE se recalcula desde los
gramos finales y la base de alimentos.
"""

from collections.abc import Callable
from dataclasses import dataclass
from math import ceil

from nutriplan.domain.errors import GenerationError
from nutriplan.domain.generation_rules import (
    CARB_GROUP,
    FAT_GROUP,
    PROTEIN_GROUP,
    SLOT_STRUCTURE,
)
from nutriplan.domain.macro_split import macro_shares
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
    MacroTargets,
    MealFoodPortion,
    MealSlot,
    UnitGranularity,
)
from nutriplan.domain.nutrition_config import NutritionConfig
from nutriplan.domain.validation import MIN_RELEVANT_G

MAX_PORTION_G = 600.0
FIXED_POINT_ITERATIONS = 10


def fits_protein(food: FoodItem, target_g: float) -> bool:
    """¿Este alimento puede cubrir la proteína del slot sin ser absurdo?

    Un alimento que se pesa en gramos siempre cabe: se porciona fino. Uno que va
    por unidades (huevo, lata, loncha) solo cabe si algún número entero o medio de
    unidades aterriza dentro de la tolerancia del slot: 2 huevos ≈ 12 g sirven
    para un snack; una lata de atún (26 g) no — se pasa del objetivo de 12 g y no
    hay forma de bajarla.

    Vive aquí, y no en el motor, porque la usan los dos: el heurístico para armar
    sus pools y el de platos para decidir qué ancla proteica es admisible.
    """
    if food.unit_granularity is UnitGranularity.GRAMS or not food.default_unit_g:
        return True
    unit_protein = food.protein_100g * food.default_unit_g / 100.0
    if unit_protein <= 0:
        return True  # su rol no es la proteína (pan, aguacate)
    step = 1.0 if food.unit_granularity is UnitGranularity.WHOLE else 0.5
    count = max(round(target_g / unit_protein / step) * step, step)
    tol = max(0.15 * target_g, MIN_RELEVANT_G)
    return abs(count * unit_protein - target_g) <= tol


def usable_in_slot(
    daily: MacroTargets, config: NutritionConfig
) -> Callable[[FoodItem, MealSlot], bool]:
    """¿Puede el motor usar este alimento en este slot?

    Solo mira la proteína, que es el macro que no se puede fraccionar: lo demás se
    porciona fino. Lo usan el motor (para armar sus pools) y la regla de variedad
    (para saber cuántas opciones REALES hay). Si no miran lo mismo, la regla juzga
    al motor por opciones que el motor no tiene.
    """
    share = config.meal_distribution

    def usable(food: FoodItem, slot: MealSlot) -> bool:
        if food.category not in PROTEIN_GROUP or slot not in share:
            return True
        return fits_protein(food, daily.protein_g * share[slot].protein_g)

    return usable


def _cap_g(food: FoodItem) -> float:
    """Tope de porción de un alimento (p. ej. aceitunas ≤ 30 g)."""
    if food.portion_max_g is not None:
        return min(food.portion_max_g, MAX_PORTION_G)
    return MAX_PORTION_G


@dataclass
class SolvedMeal:
    slot: MealSlot
    portions: list[MealFoodPortion]
    computed: MacroTargets


def _grid(food: FoodItem, cfg: NutritionConfig) -> tuple[float, float]:
    """Paso y piso de un alimento, ya alineados entre sí.

    El paso lo declara el alimento (un huevo salta de 50 en 50, el arroz de 10 en
    10): la báscula la usa una persona, y es más fácil pesar 120 o 150 g que 137.
    El piso se sube al paso — un mínimo de 20 g con un paso de 15 (una cucharada)
    devolvería 20 g, que no es ninguna cucharada real.
    """
    step = food.portion_step_g or float(cfg.portioning.grams_rounding)
    floor = food.portion_min_g or float(cfg.portioning.min_portion_g)
    return step, ceil(floor / step) * step


def _round_portion(grams: float, cfg: NutritionConfig, food: FoodItem) -> float:
    """Redondea la porción a algo que una persona sirve y pesa de verdad.

    Devuelve 0.0 si no llega ni a un paso: el llamador decide si la sube al piso
    (el slot exige ese macro) o la descarta (era opcional).
    """
    if food.is_free:
        return 0.0
    step, floor = _grid(food, cfg)
    n = round(grams / step)
    if n <= 0:
        return 0.0
    return float(min(max(n * step, floor), _cap_g(food)))


def macros_of(portions: list[tuple[FoodItem, float]]) -> MacroTargets:
    """Macros de una comida a partir de (alimento, gramos). El código es dueño
    de los números: la UI la reusa al editar porciones en línea."""
    def total(attr: str) -> float:
        return round(sum(float(getattr(f, attr)) * g / 100.0 for f, g in portions), 1)

    return MacroTargets(
        kcal=total("kcal_100g"),
        protein_g=total("protein_100g"),
        carb_g=total("carb_100g"),
        fat_g=total("fat_100g"),
        fiber_g=total("fiber_100g"),
    )


def solve_day_portions(
    meals: list[tuple[MealSlot, list[FoodItem]]],
    daily: MacroTargets,
    config: NutritionConfig,
) -> list[SolvedMeal]:
    """Resuelve los gramos de todos los slots de un día."""
    # La grasa se reparte por el peso en KCAL del slot: no tiene columna propia.
    kcal_share = config.kcal_shares()
    # Cada macro va donde hay una fuente que lo lleve: un snack de solo fruta no
    # tiene objetivo de proteína, y la suya se reparte entre las comidas grandes.
    shares = macro_shares(meals, config)

    # --- Paso 1: proteína y carbo por slot (punto fijo sobre aportes cruzados)
    grams: dict[tuple[MealSlot, str], float] = {}
    foods_of: dict[MealSlot, list[FoodItem]] = {}
    for slot, foods in meals:
        foods_of[slot] = foods
        for f in foods:
            grams[(slot, str(f.id))] = 0.0

    def slot_sources(slot: MealSlot, group: set[FoodCategory]) -> list[FoodItem]:
        return [f for f in foods_of[slot] if f.category in group]

    # Un slot sin fuente real de su macro obligatorio no se puede resolver:
    # error claro aquí en vez de un plan que "cuadra" ignorando el slot.
    for slot, _foods in meals:
        rule = SLOT_STRUCTURE[slot]
        if rule.requires_protein and not slot_sources(slot, PROTEIN_GROUP):
            raise GenerationError(f"Slot {slot.value}: sin fuente de proteína")
        if (
            rule.requires_carb
            and not rule.carb_optional
            and not slot_sources(slot, CARB_GROUP)
        ):
            raise GenerationError(f"Slot {slot.value}: sin fuente de carbohidrato")

    fat_items = [
        (slot, f) for slot, foods in meals for f in foods if f.category in FAT_GROUP
    ]

    for _ in range(FIXED_POINT_ITERATIONS):
        for slot, foods in meals:
            p_target = daily.protein_g * shares[slot]["protein_g"]
            c_target = daily.carb_g * shares[slot]["carb_g"]

            p_sources = slot_sources(slot, PROTEIN_GROUP)
            c_sources = slot_sources(slot, CARB_GROUP)

            # aportes cruzados con los gramos actuales de las otras fuentes
            p_cross = sum(
                f.protein_100g * grams[(slot, str(f.id))] / 100.0
                for f in foods
                if f not in p_sources
            )
            c_cross = sum(
                f.carb_100g * grams[(slot, str(f.id))] / 100.0
                for f in foods
                if f not in c_sources
            )

            p_needed = max(p_target - p_cross, 0.0)
            c_needed = max(c_target - c_cross, 0.0)

            for sources, needed, attr in (
                (p_sources, p_needed, "protein_100g"),
                (c_sources, c_needed, "carb_100g"),
            ):
                if not sources:
                    continue
                share = needed / len(sources)
                for f in sources:
                    density = getattr(f, attr) / 100.0
                    if density <= 0:
                        raise GenerationError(
                            f"{f.name_es} no aporta {attr}; selección inválida"
                        )
                    grams[(slot, str(f.id))] = min(share / density, _cap_g(f))

        # --- Paso 2 (dentro del punto fijo): la grasa cierra a nivel de día.
        # Un ítem de grasa puede traer proteína/carbo (maní, aguacate); al
        # iterar, las fuentes de arriba compensan ese aporte cruzado.
        fat_so_far = sum(
            f.fat_100g * grams[(slot, str(f.id))] / 100.0
            for slot, foods in meals
            for f in foods
            if f.category not in FAT_GROUP
        )
        fat_needed = max(daily.fat_g - fat_so_far, 0.0)
        # El reparto va PONDERADO por el peso del slot, no a partes iguales: un
        # snack que vale el 10% del día no puede llevarse la misma grasa que un
        # almuerzo del 30%. Con grasa permitida en los snacks, los ítems pasaron
        # de 3 a 5 y el reparto uniforme dejaba 2 cucharadas de maní a media
        # mañana y un almuerzo sin aceite.
        if fat_items:
            total_weight = sum(kcal_share.get(slot, 0.0) for slot, _f in fat_items)
            for slot, f in fat_items:
                weight = (
                    kcal_share.get(slot, 0.0) / total_weight
                    if total_weight > 0
                    else 1.0 / len(fat_items)
                )
                share = fat_needed * weight
                grams[(slot, str(f.id))] = min(share / (f.fat_100g / 100.0), _cap_g(f))

    # --- Paso 3: aterrizar en la rejilla de cada alimento
    for slot, foods in meals:
        rule = SLOT_STRUCTURE[slot]
        for f in foods:
            key = (slot, str(f.id))
            g = _round_portion(grams[key], config, f)
            if g == 0.0 and not f.is_free:
                # Redondeó a nada. Si el slot EXIGE ese macro, se sube al piso;
                # si era opcional (el carbo de la cena), se descarta — que es
                # justo la "cena sin carbohidrato" de los planes reales.
                required = (rule.requires_protein and f.category in PROTEIN_GROUP) or (
                    rule.requires_carb
                    and not rule.carb_optional
                    and f.category in CARB_GROUP
                )
                if required:
                    g = _grid(f, config)[1]
            grams[key] = g

    # --- Paso 4: reparar el residuo del redondeo.
    # El punto fijo es continuo; la rejilla no. Un huevo de más son 143 kcal, y
    # ese error no lo absorbe nadie solo. Sin este paso, los pasos gruesos sacan
    # el día de tolerancia y `generate_cycle` agota los reintentos.
    _repair_residual(grams, meals, daily, config)

    # --- Paso 5: recálculo (el código es dueño de los números)
    solved: list[SolvedMeal] = []
    for slot, foods in meals:
        final = [(f, grams[(slot, str(f.id))]) for f in foods
                 if grams[(slot, str(f.id))] > 0]
        if not final:
            raise GenerationError(f"Slot {slot.value}: ninguna porción resuelta")
        solved.append(
            SolvedMeal(
                slot=slot,
                portions=[MealFoodPortion(food_id=f.id, grams=g) for f, g in final],
                computed=macros_of(final),
            )
        )
    return solved


_MACRO_ATTR = {
    "kcal": "kcal_100g",
    "protein_g": "protein_100g",
    "carb_g": "carb_100g",
    "fat_g": "fat_100g",
}
MAX_REPAIR_MOVES = 80

Key = tuple[MealSlot, str]


@dataclass
class _Term:
    """Un objetivo que la reparación intenta cumplir.

    Son exactamente los que mira `validate_day`: los 4 macros del día, y la
    proteína y el carbohidrato de cada slot. Si solo se optimizara el día, la
    reparación cuadraría el total robándole el carbohidrato al desayuno —
    aritmética impecable, comida sin sentido.

    El coste replica el criterio del validador, banda muerta incluida: dentro de
    la tolerancia no cuesta nada (el objetivo ya está cumplido, no hay que
    afinar más), y fuera crece rápido. Sin la banda, la reparación optimizaría
    términos que al validador le dan igual a costa de los que no.
    """

    target: float
    allowance: float  # lo que `validate_day` deja pasar sin quejarse
    density: dict[Key, float]  # aporte por gramo de cada ítem
    actual: float = 0.0

    def cost(self, actual: float) -> float:
        off = abs(actual - self.target) / self.allowance
        excess = max(0.0, off - 1.0)
        # El segundo término solo desempata: dentro de la banda hay un gradiente
        # suave hacia el centro, para que la búsqueda no se quede en una meseta.
        return excess**2 + 1e-3 * off**2


def _repair_residual(
    grams: dict[Key, float],
    meals: list[tuple[MealSlot, list[FoodItem]]],
    daily: MacroTargets,
    config: NutritionConfig,
    locked: frozenset[Key] = frozenset(),
) -> None:
    """Descenso por coordenadas sobre la rejilla, hasta que ningún paso mejore.

    Determinista y sin aleatoriedad: el orden de los candidatos es fijo, así que
    el mismo día siempre repara igual. Se prueban de más fino a más grueso — el
    pollo (paso 10 g) absorbe el error que el huevo (paso 50 g) no puede.
    """
    tol = config.tolerances
    tolerances = {
        "kcal": tol.kcal,
        "protein_g": tol.protein_g,
        "carb_g": tol.carb_g,
        "fat_g": tol.fat_g,
    }
    items: list[tuple[Key, FoodItem]] = [
        ((slot, str(f.id)), f) for slot, foods in meals for f in foods if not f.is_free
    ]

    terms: list[_Term] = []
    for macro, attr in _MACRO_ATTR.items():
        target = getattr(daily, macro)
        if target > 0:
            allowance = target * tolerances[macro]
            terms.append(
                _Term(target, allowance, {k: getattr(f, attr) / 100.0 for k, f in items})
            )
    # Por slot solo proteína y carbo, igual que validate_day — con el MISMO
    # reparto (el de `macro_shares`) y su mismo piso absoluto: un objetivo de
    # 9.9 g admite ±10 g. Antes estos términos se SALTABAN por pequeños, así que
    # la reparación inflaba el requesón del snack a 200 g sin coste alguno
    # mientras el validador sí lo rechazaba.
    shares = macro_shares(meals, config)
    for slot, foods in meals:
        for macro in ("protein_g", "carb_g"):
            # Un slot sin fuente de ese macro no tiene objetivo: no se le pide lo
            # que no puede dar (el snack de solo fruta no debe proteína).
            target = getattr(daily, macro) * shares[slot][macro]
            if target <= 0:
                continue
            attr = _MACRO_ATTR[macro]
            allowance = max(target * tolerances[macro], MIN_RELEVANT_G)
            terms.append(
                _Term(
                    target,
                    allowance,
                    {(slot, str(f.id)): getattr(f, attr) / 100.0 for f in foods if not f.is_free},
                )
            )

    terms_of: dict[Key, list[_Term]] = {k: [] for k, _ in items}
    for term in terms:
        term.actual = sum(d * grams[k] for k, d in term.density.items())
        for k in term.density:
            terms_of[k].append(term)

    candidates = sorted(
        (pair for pair in items if pair[0] not in locked),
        key=lambda pair: (pair[1].portion_step_g, pair[1].name_es, pair[0][1]),
    )
    if not candidates:
        return

    for _ in range(MAX_REPAIR_MOVES):
        best_gain = 1e-9
        move: tuple[Key, float, list[tuple[_Term, float]]] | None = None
        for key, food in candidates:
            current = grams[key]
            step, floor = _grid(food, config)
            for delta in (step, -step):
                new = current + delta
                # No se baja un ítem por debajo de su piso ni se resucita uno
                # descartado: eso lo decidió el redondeo, aquí solo se afina.
                cap = _cap_g(food)
                if new < floor or new > cap or current == 0.0:
                    continue
                gain, updates = 0.0, []
                for term in terms_of[key]:
                    actual = term.actual + term.density[key] * delta
                    gain += term.cost(term.actual) - term.cost(actual)
                    updates.append((term, actual))
                if gain > best_gain:
                    best_gain, move = gain, (key, new, updates)
        if move is None:
            return  # óptimo local en la rejilla: ningún paso mejora
        key, new, updates = move
        grams[key] = new
        for term, actual in updates:
            term.actual = actual


def rebalance_day_after_edit(
    meals: list[tuple[MealSlot, list[FoodItem]]],
    grams: dict[Key, float],
    daily: MacroTargets,
    config: NutritionConfig,
    *,
    locked: frozenset[Key],
) -> dict[Key, float]:
    """Tras editar un slot, compensa el día: lo editado queda fijo, el resto ajusta."""
    working = dict(grams)
    _repair_residual(working, meals, daily, config, locked=locked)
    return working
