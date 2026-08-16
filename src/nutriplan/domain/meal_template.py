"""Platos: el catálogo de COMBINACIONES coherentes (no de alimentos sueltos).

El motor armaba cada comida rol por rol —una proteína, un carbo, una grasa—
tomándolos de listas ordenadas alfabéticamente. Cuadraba los macros y no sabía
nada de cocina: nada impedía "yogur + pan" de desayuno ni "granola + queso
crema". No existía ninguna noción de qué va con qué.

Un PLATO es esa noción, como dato. Declara sus componentes por ROL, y cada
componente apunta a un alimento concreto (`#aguacate`) o a una CLASE de alimentos
(`@lean_meat`). Al expandirlo contra lo que el cliente tiene permitido salen
todas las versiones concretas del plato que se pueden cocinar para él.

El plato es un dispositivo de COHERENCIA EN TIEMPO DE GENERACIÓN: restringe qué
combinaciones de alimentos pueden emitirse y muere ahí. No se persiste, no es un
invariante del dominio y el entrenador puede romperlo editando a mano — como debe
ser: manda el humano.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from itertools import product
from uuid import UUID

from nutriplan.domain.generation_rules import SLOT_STRUCTURE
from nutriplan.domain.models import FoodCategory, FoodItem, MealSlot, UnitGranularity

# Tope de componentes por plato. No es una preferencia: `build_selection_schema`
# limita `food_ids` a 4 por comida, así que un plato de 5 lo rechazaría Pydantic
# en el borde.
MAX_COMPONENTS = 4


@dataclass(frozen=True)
class FoodClass:
    """Un predicado sobre el catálogo. Los campos vacíos no filtran nada."""

    name: str
    label: str = ""
    categories: frozenset[FoodCategory] = frozenset()
    all_tags: frozenset[str] = frozenset()
    any_tags: frozenset[str] = frozenset()
    none_tags: frozenset[str] = frozenset()
    slots_any: frozenset[MealSlot] = frozenset()
    names_any: frozenset[str] = frozenset()
    min_per_100g: Mapping[str, float] = field(default_factory=dict)
    max_per_100g: Mapping[str, float] = field(default_factory=dict)
    max_ratio: Mapping[str, tuple[str, float]] = field(default_factory=dict)
    unit_granularity: UnitGranularity | None = None
    max_unit_g: float | None = None

    def matches(self, food: FoodItem, slot: MealSlot) -> bool:
        if self.categories and food.category not in self.categories:
            return False
        tags = set(food.tags)
        if self.all_tags and not self.all_tags <= tags:
            return False
        if self.any_tags and not (self.any_tags & tags):
            return False
        if self.none_tags & tags:
            return False
        if self.names_any and food.name_es not in self.names_any:
            return False
        # La afinidad por comida la declara el alimento; la clase solo puede
        # estrecharla, nunca ensancharla.
        if slot not in food.meal_slots:
            return False
        if self.slots_any and not (self.slots_any & set(food.meal_slots)):
            return False
        for attr, minimum in self.min_per_100g.items():
            if getattr(food, attr) < minimum:
                return False
        for attr, maximum in self.max_per_100g.items():
            if getattr(food, attr) > maximum:
                return False
        for attr, (other, factor) in self.max_ratio.items():
            if getattr(food, attr) > factor * getattr(food, other):
                return False
        if self.unit_granularity and food.unit_granularity is not self.unit_granularity:
            return False
        if self.max_unit_g is not None:
            unit = food.default_unit_g
            if unit is None or unit > self.max_unit_g:
                return False
        return True


@dataclass(frozen=True)
class Component:
    role: FoodCategory
    selector: str  # "@lean_meat" (clase) o "#aguacate" (alimento concreto)
    optional: bool = False


@dataclass(frozen=True)
class MealTemplate:
    id: str
    name: str
    slots: tuple[MealSlot, ...]
    components: tuple[Component, ...]
    free_salad: bool = False
    # Cómo se prepara, escrita a mano. Sin esto el modo offline no tiene receta.
    recipe: tuple[str, ...] = ()
    # Cómo se llama el plato ya resuelto: "Salteado de {protein} con {carb}". El
    # `name` de arriba describe el FORMATO y sirve para hablar de la plantilla en
    # la consola; lo que la persona lee en su semana sale de aquí, con sus
    # alimentos dentro. Sin patrón, el nombre se arma con la lista de alimentos.
    naming: str = ""


@dataclass(frozen=True)
class Dish:
    """Un plato ya resuelto: la tupla concreta de alimentos que se va a servir."""

    template_id: str
    name: str
    slot: MealSlot
    foods: tuple[FoodItem, ...]
    free_salad: bool = False
    # Componentes opcionales que este plato NO lleva (la cena sin aguacate). El
    # selector lo penaliza flojito: la versión completa es la buena salvo que
    # servirla implique repetir un alimento del día.
    dropped: int = 0

    @property
    def food_ids(self) -> tuple[UUID, ...]:
        return tuple(f.id for f in self.foods)

    @property
    def anchor(self) -> FoodItem:
        """El alimento que define el plato: su proteína, o el primero que haya."""
        for food in self.foods:
            if food.category in (FoodCategory.PROTEIN, FoodCategory.DAIRY):
                return food
        return self.foods[0]

    @property
    def key(self) -> tuple[str, ...]:
        """Clave de desempate estable: el mismo plato siempre ordena igual."""
        return (self.template_id, *sorted(str(f.id) for f in self.foods))


# Cosmética de títulos, no contabilidad: de cuánto pesa cocido se encarga
# `FoodItem.state` y el factor de rendimiento. Esto solo existe para las filas
# viejas que llevan el estado metido en el nombre («arroz blanco cocido»), que
# en un menú se lee «arroz blanco»; las que importa el catálogo nuevo ya vienen
# nombradas en limpio. El empaque («en agua», «en aceite») se deja: es lo que
# distingue el atún de lata, y sin él dos platos distintos acabarían con el
# mismo título.
_COOKED_SUFFIXES = (" cocidos", " cocidas", " cocido", " cocida")


def kitchen_name(name_es: str) -> str:
    """Cómo se nombra el alimento en un título de menú, no en el catálogo."""
    n = name_es.strip()
    changed = True
    while changed:
        changed = False
        lower = n.lower()
        for suffix in _COOKED_SUFFIXES:
            if lower.endswith(suffix):
                n = n[: -len(suffix)].rstrip()
                changed = True
                break
    return n


def is_inedible_title(name: str) -> bool:
    """Sopa de atún de lata o de plátano: nadie la pediría en un restaurante."""
    folded = "".join(
        c for c in unicodedata.normalize("NFD", name.lower()) if unicodedata.category(c) != "Mn"
    )
    if "sopa" not in folded:
        return False
    return any(word in folded for word in ("atun", "sardina", "platano"))


def _name_from_foods(foods: tuple[FoodItem, ...]) -> str:
    """El nombre de reserva: lo que hay en el plato, tal cual."""
    if not foods:
        return "Plato"
    names = [kitchen_name(f.name_es) for f in foods]
    head, *rest = names
    if not rest:
        return head.capitalize()
    if len(rest) == 1:
        return f"{head.capitalize()} con {rest[0]}"
    return f"{head.capitalize()} con " + ", ".join(rest[:-1]) + f" y {rest[-1]}"


def dish_name(template: MealTemplate, chosen: Sequence[FoodItem | None]) -> str:
    """Cómo se llama este plato concreto.

    El nombre de la plantilla describe el formato ("Proteína con carbohidrato y
    ensalada") y era el que acababa en la pantalla: cinco almuerzos distintos se
    leían iguales y la semana parecía repetida aunque debajo cambiaran la carne y
    el carbohidrato. Ahora el título lleva dentro lo que se va a comer.

    Si el patrón nombra un componente que hoy no está —la grasa opcional que el
    solver dejó fuera—, se cae al nombre por alimentos antes que prometer algo
    que no viene en el plato.
    """
    por_rol = {
        c.role.value: kitchen_name(f.name_es)
        for c, f in zip(template.components, chosen, strict=True)
        if f is not None
    }
    foods = tuple(f for f in chosen if f is not None)
    if not template.naming:
        return _name_from_foods(foods)
    try:
        nombre = template.naming.format(**por_rol)
    except (KeyError, IndexError):
        return _name_from_foods(foods)
    return nombre[:1].upper() + nombre[1:]


@dataclass(frozen=True)
class MealCatalog:
    version: str  # entra en compute_input_hash: si cambia, se regenera el plan
    classes: Mapping[str, FoodClass]
    templates: tuple[MealTemplate, ...]


class MealCatalogError(ValueError):
    """El catálogo de platos es inconsistente. Se lanza al CARGAR, no al generar."""


# Qué categorías puede cubrir cada rol. Un componente de rol `protein` puede
# resolverse a un lácteo (el yogur del desayuno es la proteína de ese plato).
ROLE_CATEGORIES: dict[FoodCategory, frozenset[FoodCategory]] = {
    FoodCategory.PROTEIN: frozenset({FoodCategory.PROTEIN, FoodCategory.DAIRY}),
    FoodCategory.CARB: frozenset({FoodCategory.CARB}),
    FoodCategory.FRUIT: frozenset({FoodCategory.FRUIT}),
    FoodCategory.FAT: frozenset({FoodCategory.FAT}),
}


def candidates_for(
    catalog: MealCatalog,
    component: Component,
    allowed: Sequence[FoodItem],
    slot: MealSlot,
    *,
    on_hand: frozenset[UUID] = frozenset(),
) -> list[FoodItem]:
    """Los alimentos permitidos que satisfacen un componente en un slot.

    Ordenados por AFINIDAD primero (`FoodItem.weight_in`), y el nombre solo
    desempata. Importa porque `expand` trunca esta lista a `per_component`: con un
    orden alfabético, "arepa de maíz" entraba en el almuerzo por la A y echaba del
    corte a un carbo que sí es de almuerzo. Si solo caben seis, que sean los seis
    que mejor encajan en esa comida — no los seis primeros del diccionario.

    `on_hand` —lo que la persona dice tener en casa— va DELANTE de la afinidad por
    ese mismo truncado: preferirlo solo en la función de coste no basta si el
    alimento no llega a estar en ningún plato del pool. Aquí solo decide quién
    entra; que ganar o no lo sigue decidiendo el coste, con la variedad y el
    sentido común de cocina por encima.
    """
    selector = component.selector
    if selector.startswith("#"):
        name = selector[1:]
        found = [f for f in allowed if f.name_es == name and slot in f.meal_slots]
    else:
        food_class = catalog.classes[selector.lstrip("@")]
        found = [f for f in allowed if food_class.matches(f, slot)]
    valid = ROLE_CATEGORIES[component.role]
    return sorted(
        (f for f in found if f.category in valid),
        key=lambda f: (f.id not in on_hand, -f.weight_in(slot), f.name_es, str(f.id)),
    )


@dataclass(frozen=True)
class PoolWarning:
    slot: MealSlot
    dish_count: int
    minimum: int


def expand(
    catalog: MealCatalog,
    allowed: Sequence[FoodItem],
    *,
    admissible: object = None,
    dish_admissible: object = None,
    per_component: int = 6,
    on_hand: frozenset[UUID] = frozenset(),
) -> dict[MealSlot, list[Dish]]:
    """Todas las versiones concretas de cada plato que el cliente puede comer.

    `admissible(food, slot) -> bool` filtra el ancla proteica por sí sola (media
    lata de atún no cuadra un snack).

    `dish_admissible(dish) -> bool` la filtra DENTRO DE SU PLATO, que es donde de
    verdad se juega. Un alimento que va por unidades no se puede afinar, así que la
    proteína que aportan sus acompañantes decide si aterriza o no: la lata de atún
    (25.5 g por lata) "cabe" en un almuerzo de 46.7 g mirándola sola —dos latas son
    51, dentro de tolerancia—, pero con el arroz y el aguacate del plato el total
    sube a 57.6 y el día entero deja de cuadrar. Mirando el ancla en aislamiento,
    ese plato se emitía igual y la generación fallaba cuatro intentos después, sin
    decir por qué.

    Si el filtro deja un slot sin platos, se ignora: más vale un plan difícil de
    cuadrar que ningún plan.

    El truncado (`per_component`) se aplica POR COMPONENTE, no sobre el producto
    ya generado: un plato de 3 componentes con clases de 20 alimentos son 8.000
    combinaciones, y recortar al final por orden alfabético haría que "aguacate"
    saliera siempre y "tahini" nunca.

    `on_hand` entra en ese corte: lo que la persona ya tiene en casa se asegura un
    sitio en el pool (ver `candidates_for`).
    """
    pools: dict[MealSlot, list[Dish]] = {slot: [] for slot in MealSlot}
    rejected: dict[MealSlot, list[Dish]] = {slot: [] for slot in MealSlot}

    for template in catalog.templates:
        for slot in template.slots:
            choices: list[list[FoodItem | None]] = []
            satisfiable = True
            for component in template.components:
                found = candidates_for(catalog, component, allowed, slot, on_hand=on_hand)
                if component.role is FoodCategory.PROTEIN and callable(admissible):
                    kept = [f for f in found if admissible(f, slot)]
                    found = kept or found
                if not found:
                    if component.optional:
                        choices.append([None])
                        continue
                    satisfiable = False
                    break
                options: list[FoodItem | None] = list(found[:per_component])
                # "Opcional" significaba "si no hay nada que lo cubra, se sirve sin
                # él" — o sea, nunca: en cuanto había UN candidato, el componente
                # era obligatorio. El plato quedaba sin la versión que la plantilla
                # promete ("+ grasa opcional"), y con ella la única salida de un
                # cliente cuya única grasa de cena es el aguacate: se lo comía en el
                # almuerzo Y en la cena, y el día se pasaba de grasa sin remedio.
                if component.optional:
                    options.append(None)
                choices.append(options)
            if not satisfiable:
                continue

            for combo in product(*choices):
                foods = tuple(f for f in combo if f is not None)
                if not foods:
                    continue
                # Un alimento no puede cubrir dos componentes del mismo plato
                # (aguacate de grasa Y de fruta: el solver le pediría dos gramajes).
                if len({f.id for f in foods}) != len(foods):
                    continue
                dish = Dish(
                    template_id=template.id,
                    name=dish_name(template, combo),
                    slot=slot,
                    foods=foods,
                    free_salad=template.free_salad,
                    dropped=len(template.components) - len(foods),
                )
                if callable(dish_admissible) and not dish_admissible(dish):
                    rejected[slot].append(dish)
                    continue
                pools[slot].append(dish)

    for slot in pools:
        # Si de este slot no sobrevivió ningún plato, el filtro se rinde: el
        # cliente come lo que tiene, y un plan que cuesta cuadrar es mejor que
        # `InsufficientDishes`.
        if not pools[slot] and rejected[slot]:
            pools[slot] = rejected[slot]
        pools[slot].sort(key=lambda d: d.key)
    return pools


def pool_health(pools: Mapping[MealSlot, Iterable[Dish]], *, minimum: int = 4) -> list[PoolWarning]:
    """Los slots con tan pocos platos que van a repetirse sí o sí.

    Como el universo del plan sigue siendo lo que el cliente marcó que le gusta,
    una lista pobre condena a repetir. Esto lo hace VISIBLE al entrenador en vez
    de producir yogur siete días en silencio.
    """
    warnings: list[PoolWarning] = []
    for slot in pools:  # solo las comidas que el cliente come
        count = len(list(pools.get(slot, ())))
        if count < minimum:
            warnings.append(PoolWarning(slot=slot, dish_count=count, minimum=minimum))
    return warnings


def validate_catalog(catalog: MealCatalog, universe: Sequence[FoodItem]) -> None:
    """Invariantes del catálogo de platos. Falla al ARRANCAR, no en generación.

    Lo importante: cada componente tiene que poder resolverse a alimentos de una
    categoría compatible con su rol. Así todo plato satisface
    `validate_selection_structure` POR CONSTRUCCIÓN, y no hace falta añadir otra
    regla al validador que pueda desincronizarse del catálogo.
    """
    if not catalog.templates:
        raise MealCatalogError("El catálogo de platos está vacío.")

    for template in catalog.templates:
        if not template.slots:
            raise MealCatalogError(f"Plato '{template.id}': sin slots.")
        if not template.components:
            raise MealCatalogError(f"Plato '{template.id}': sin componentes.")
        if len(template.components) > MAX_COMPONENTS:
            raise MealCatalogError(
                f"Plato '{template.id}': {len(template.components)} componentes; "
                f"el esquema de selección solo admite {MAX_COMPONENTS} alimentos por comida."
            )
        # Y tampoco puede pasarse del tope de SU comida: un snack de tres alimentos
        # carga sin protestar y luego `validate_selection_structure` lo rechaza a
        # mitad de la generación, que agota los reintentos y falla el plan entero.
        for slot in template.slots:
            allowed_items = SLOT_STRUCTURE[slot].max_items
            if len(template.components) > allowed_items:
                raise MealCatalogError(
                    f"Plato '{template.id}' en {slot.value}: {len(template.components)} "
                    f"componentes; esa comida admite {allowed_items}."
                )
        for component in template.components:
            if component.role not in ROLE_CATEGORIES:
                raise MealCatalogError(
                    f"Plato '{template.id}': rol '{component.role.value}' no válido."
                )
            selector = component.selector
            if selector.startswith("@"):
                key = selector[1:]
                if key not in catalog.classes:
                    raise MealCatalogError(f"Plato '{template.id}': la clase '@{key}' no existe.")
            elif selector.startswith("#"):
                name = selector[1:]
                if not any(f.name_es == name for f in universe):
                    raise MealCatalogError(
                        f"Plato '{template.id}': el alimento '{name}' no está en el catálogo."
                    )
            else:
                raise MealCatalogError(
                    f"Plato '{template.id}': selector '{selector}' debe empezar por @ o #."
                )

            # Ningún alimento del universo puede satisfacer el componente con una
            # categoría incompatible con su rol.
            valid = ROLE_CATEGORIES[component.role]
            for slot in template.slots:
                for food in _raw_matches(catalog, selector, universe, slot):
                    if food.category not in valid:
                        raise MealCatalogError(
                            f"Plato '{template.id}', componente {component.role.value}: "
                            f"'{food.name_es}' es {food.category.value}, incompatible con el rol."
                        )


def _raw_matches(
    catalog: MealCatalog, selector: str, universe: Sequence[FoodItem], slot: MealSlot
) -> list[FoodItem]:
    if selector.startswith("#"):
        name = selector[1:]
        return [f for f in universe if f.name_es == name]
    food_class = catalog.classes[selector.lstrip("@")]
    return [f for f in universe if food_class.matches(f, slot)]


def static_recipes(catalog: MealCatalog) -> dict[str, list[str]]:
    """Las recetas escritas a mano, por id de plantilla.

    Son el suelo: sin IA, o cuando el modelo no contesta, la app sigue sabiendo
    explicar cómo se prepara cada plato.
    """
    return {t.id: list(t.recipe) for t in catalog.templates if t.recipe}
