"""Cómo una nota libre apunta a alimentos del catálogo.

La persona escribe "pollo sudado con papa". Las palabras de técnica no son
alimento; las de relleno tampoco. Lo que queda se busca en el nombre y los
alias del plato. Si no hay coincidencia, no se inventa un bacalao.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nutriplan.domain.food_matching import match_food_names, normalize
from nutriplan.domain.generation_rules import CARB_GROUP
from nutriplan.domain.meal_template import Dish, _name_from_foods
from nutriplan.domain.models import FoodItem, MealSlot

_STOP = frozenset(
    {
        "con",
        "para",
        "algo",
        "este",
        "esta",
        "esto",
        "menos",
        "más",
        "mas",
        "quiero",
        "queria",
        "quería",
        "gustaria",
        "gustaría",
        "un",
        "una",
        "unos",
        "unas",
        "el",
        "la",
        "los",
        "las",
        "de",
        "del",
        "al",
        "y",
        "o",
        "que",
        "me",
        "te",
        "en",
        "por",
        "mi",
        "su",
        "tu",
        "como",
        "muy",
        "otro",
        "otra",
        "lugar",
        "quisiera",
        "comer",
        "comida",
        "comidas",
        "plato",
        "platos",
        "menu",
        "menú",
        "favor",
        "preferiria",
        "preferiría",
        "desayuno",
        "almuerzo",
        "cena",
        "snack",
        "hoy",
        "solo",
        "sólo",
        "tambien",
        "también",
        "tiene",
        "tenga",
        "algun",
        "algún",
        "alguna",
    }
)

_TECHNIQUE = frozenset(
    {
        "sudado",
        "frito",
        "frita",
        "dorado",
        "dorada",
        "asado",
        "asada",
        "horneado",
        "horneada",
        "guisado",
        "guisada",
        "salteado",
        "salteada",
        "ajillo",
        "vapor",
        "plancha",
        "horno",
        "cocido",
        "cocida",
        "crudo",
        "cruda",
        "grill",
        "wok",
    }
)


_KEEP_SAME = frozenset({"mismo", "misma", "igual"})
_FRIES_WORDS = frozenset({"francesa", "francesas"})


@dataclass(frozen=True)
class SwapIntent:
    """Lo que una nota simple pide, sin llamar a un modelo."""

    keep_same: bool
    include_note: str
    exclude: tuple[str, ...]
    missing_dish: bool


def _words(note: str) -> list[str]:
    return (
        note.lower().replace(",", " ").replace(".", " ").replace("«", " ").replace("»", " ").split()
    )


def _fix_typos(words: list[str]) -> list[str]:
    """«plan blanco» junto a «sin» es pan, no un plan de entrenamiento."""
    out: list[str] = []
    for i, word in enumerate(words):
        if word == "plan" and (
            (i + 1 < len(words) and words[i + 1].startswith("blanc"))
            or (i > 0 and words[i - 1] == "sin")
        ):
            out.append("pan")
        else:
            out.append(word)
    return out


def _food_hay(food: FoodItem) -> str:
    return " ".join([food.name_es.lower(), *food.aliases])


def _is_fries(food: FoodItem) -> bool:
    hay = _food_hay(food)
    return "francesa" in hay or re.search(r"papa\w*.*frit|frit\w*.*papa", hay) is not None


def _is_potato(food: FoodItem) -> bool:
    hay = _food_hay(food)
    return food.category in CARB_GROUP and _token_hit("papa", hay) and not _is_fries(food)


def _catalog_has_fries(catalog: list[FoodItem]) -> bool:
    return any(_is_fries(food) for food in catalog)


def asks_for_fries(note: str) -> bool:
    """Papas a la francesa / fritas: la persona pidió ese plato, no papa cocida."""
    blob = note.lower()
    words = set(_words(note))
    francesa = bool(words & _FRIES_WORDS) or "francesa" in blob
    papas_fritas = bool(words & {"papa", "papas"}) and bool(
        words & {"frita", "fritas", "frito", "fritos"}
    )
    return francesa or papas_fritas


def asks_missing_fries(note: str, catalog: list[FoodItem]) -> bool:
    """True si pidió fritas y el catálogo no las tiene. Ya no es un veto."""
    return asks_for_fries(note) and not _catalog_has_fries(catalog)


def closest_fries_or_potato(catalog: list[FoodItem]) -> FoodItem | None:
    """Frita si está en el pool; si no, una papa. Prefiere lo listable."""
    fries = [food for food in catalog if _is_fries(food)]
    if fries:
        fries.sort(key=lambda food: (not food.engine_default, food.name_es))
        return fries[0]
    papas = [food for food in catalog if _is_potato(food)]
    if not papas:
        return None
    papas.sort(
        key=lambda food: (
            not food.engine_default,
            "cocid" not in food.name_es.lower(),
            food.name_es,
        )
    )
    return papas[0]


def parse_swap_note(note: str, catalog: list[FoodItem]) -> SwapIntent:
    """Interpreta «lo mismo sin X» y «con Y» en código, antes de ranking o IA."""
    words = _fix_typos(_words(note))
    keep = any(word in _KEEP_SAME for word in words)
    exclude: list[str] = []
    include_words: list[str] = []
    i = 0
    while i < len(words):
        word = words[i]
        if word == "sin" and i + 1 < len(words):
            i += 1
            while i < len(words) and words[i] not in {"con", "sin"} and words[i] not in _KEEP_SAME:
                token = words[i]
                if len(token) > 2 and token not in _STOP and token not in _TECHNIQUE:
                    exclude.append(token)
                i += 1
            continue
        if word in _KEEP_SAME:
            i += 1
            continue
        include_words.append(word)
        i += 1
    return SwapIntent(
        keep_same=keep,
        include_note=" ".join(include_words),
        exclude=tuple(dict.fromkeys(exclude)),
        missing_dish=asks_missing_fries(note, catalog),
    )


def without_excluded(foods: list[FoodItem], needles: tuple[str, ...]) -> list[FoodItem]:
    if not needles:
        return list(foods)
    banned = list(needles)
    return [food for food in foods if not food_hits_needles(food, banned)]


def dish_avoids_needles(dish: Dish, needles: tuple[str, ...]) -> bool:
    if not needles:
        return True
    banned = list(needles)
    return not any(food_hits_needles(food, banned) for food in dish.foods)


def has_technique(note: str) -> bool:
    """True si la persona nombró cómo quiere que se cocine, no solo el alimento."""
    words = note.lower().replace(",", " ").replace(".", " ").split()
    return any(word in _TECHNIQUE for word in words)


def food_needles(note: str) -> list[str]:
    """Alimentos que la nota pide, sin stopwords ni técnicas de cocina.

    `papas` se singulariza a `papa` para que el matcher y la búsqueda profunda
    peguen el alimento del catálogo, no un token que no existe.
    """
    words = note.lower().replace(",", " ").replace(".", " ").split()
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        if word in _STOP or word in _TECHNIQUE or word == "sin":
            continue
        stem = normalize(word)
        if len(stem) <= 2 or stem in _TECHNIQUE or stem in seen:
            continue
        seen.add(stem)
        out.append(stem)
    return out


def _haystack(dish: Dish) -> str:
    parts = [dish.name.lower()]
    for food in dish.foods:
        parts.append(food.name_es.lower())
        parts.extend(alias.lower() for alias in food.aliases)
    return " ".join(parts)


def _token_hit(needle: str, hay: str) -> bool:
    """`papa` pega papa/papas, no papaya. `papas` también pega papa."""
    stem = normalize(needle)
    if not stem:
        return False
    return re.search(rf"(?<!\w){re.escape(stem)}s?(?!\w)", hay) is not None


def food_hits_needles(food: FoodItem, needles: list[str]) -> bool:
    hay = " ".join([food.name_es.lower(), *(alias.lower() for alias in food.aliases)])
    return any(_token_hit(needle, hay) for needle in needles)


def dish_matches_note(dish: Dish, needles: list[str]) -> bool:
    """True si el plato cubre todos los alimentos pedidos."""
    if not needles:
        return True
    hay = _haystack(dish)
    return all(_token_hit(needle, hay) for needle in needles)


def foods_from_note(note: str, catalog: list[FoodItem]) -> list[FoodItem]:
    """Resuelve la prosa a alimentos del catálogo, sin adivinar macros.

    Primero n-gramas («pechuga de pollo»), luego las palabras que quedan.
    «Quisiera comer…» no cuenta: esas palabras están en el stop.
    """
    words = food_needles(note)
    if not words:
        return []
    phrases: list[str] = []
    for size in (3, 2, 1):
        for i in range(len(words) - size + 1):
            phrases.append(" ".join(words[i : i + size]))
    listed = [food for food in catalog if food.engine_default]
    found = match_food_names(phrases, listed or catalog)
    if found.unrecognized:
        extra = match_food_names(found.unrecognized, catalog)
        found.matched.update(extra.matched)
    seen: set[object] = set()
    out: list[FoodItem] = []
    for food in found.matched.values():
        if food.id in seen:
            continue
        seen.add(food.id)
        out.append(food)
    return with_fries_or_potato(note, out, catalog)


def with_fries_or_potato(
    note: str, mapped: list[FoodItem], catalog: list[FoodItem]
) -> list[FoodItem]:
    """Una papa (o frita), no dos. Si pidió francesa y no hay frita, papa."""
    potatoes = [food for food in mapped if _is_potato(food) or _is_fries(food)]
    if not asks_for_fries(note) and len(potatoes) <= 1:
        return list(mapped)
    keep = closest_fries_or_potato(catalog) or (potatoes[0] if potatoes else None)
    rest = [food for food in mapped if not _is_potato(food) and not _is_fries(food)]
    if keep is None:
        return rest
    return rest + [keep]


def dish_from_foods(slot: MealSlot, foods: list[FoodItem], *, name: str = "") -> Dish | None:
    """Un plato improvisado con lo que la persona nombró. Máximo 4."""
    picked = foods[:4]
    if not picked:
        return None
    title = name.strip()[:80] or _name_from_foods(tuple(picked))
    return Dish(
        template_id="wish",
        name=title,
        slot=slot,
        foods=tuple(picked),
    )
