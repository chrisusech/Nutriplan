"""Cómo una nota libre apunta a alimentos del catálogo.

La persona escribe "pollo sudado con papa". Las palabras de técnica no son
alimento; las de relleno tampoco. Lo que queda se busca en el nombre y los
alias del plato. Si no hay coincidencia, no se inventa un bacalao.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nutriplan.domain.food_matching import match_food_names
from nutriplan.domain.meal_template import Dish
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


def _catalog_has_fries(catalog: list[FoodItem]) -> bool:
    for food in catalog:
        hay = " ".join([food.name_es.lower(), *food.aliases])
        if "francesa" in hay:
            return True
        if re.search(r"papa\w*.*frit|frit\w*.*papa", hay):
            return True
    return False


def asks_missing_fries(note: str, catalog: list[FoodItem]) -> bool:
    """Papas a la francesa / fritas: si no están en el CSV, no se sustituye papa cocida."""
    blob = note.lower()
    words = set(_words(note))
    francesa = bool(words & _FRIES_WORDS) or "francesa" in blob
    papas_fritas = bool(words & {"papa", "papas"}) and bool(
        words & {"frita", "fritas", "frito", "fritos"}
    )
    if not francesa and not papas_fritas:
        return False
    return not _catalog_has_fries(catalog)


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
    """Alimentos que la nota pide, sin stopwords ni técnicas de cocina."""
    words = note.lower().replace(",", " ").replace(".", " ").split()
    out: list[str] = []
    seen: set[str] = set()
    for word in words:
        if len(word) <= 2 or word in _STOP or word in _TECHNIQUE or word == "sin":
            continue
        if word in seen:
            continue
        seen.add(word)
        out.append(word)
    return out


def _haystack(dish: Dish) -> str:
    parts = [dish.name.lower()]
    for food in dish.foods:
        parts.append(food.name_es.lower())
        parts.extend(alias.lower() for alias in food.aliases)
    return " ".join(parts)


def _token_hit(needle: str, hay: str) -> bool:
    """`papa` pega papa/papas, no papaya."""
    return re.search(rf"(?<!\w){re.escape(needle)}s?(?!\w)", hay) is not None


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
    found = match_food_names(phrases, catalog)
    seen: set[object] = set()
    out: list[FoodItem] = []
    for food in found.matched.values():
        if food.id in seen:
            continue
        seen.add(food.id)
        out.append(food)
    return out


def dish_from_foods(slot: MealSlot, foods: list[FoodItem], *, name: str = "") -> Dish | None:
    """Un plato improvisado con lo que la persona nombró. Máximo 4."""
    picked = foods[:4]
    if not picked:
        return None
    title = name.strip()[:80] or " con ".join(f.name_es for f in picked)
    return Dish(
        template_id="wish",
        name=title,
        slot=slot,
        foods=tuple(picked),
    )
