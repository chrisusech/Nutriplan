"""Mapeo determinista texto → FoodItem (sección 8.4).

Los alimentos del intake llegan como texto libre ("pollo", "arroz integral").
Se resuelven contra la base por matching exacto → normalizado → fuzzy con
umbral. Lo que no matchea se marca como no reconocido para que el entrenador
lo mapee o lo cree como alimento custom. Nunca se adivina un macro.
"""

import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from nutriplan.domain.models import FoodItem

FUZZY_THRESHOLD = 0.80


def normalize(text: str) -> str:
    """minúsculas, sin acentos, espacios colapsados, plural simple removido."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = text.lower().split()
    return " ".join(_singular(w) for w in words)


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("es") and not word.endswith("nes"):
        # "arroces" → "arroz" no lo cubre; regla simple: quitar "es" tras consonante
        if word[-3] not in "aeiou":
            return word[:-2]
    if len(word) > 3 and word.endswith("s") and word[-2] in "aeiou":
        return word[:-1]
    return word


@dataclass
class MatchResult:
    matched: dict[str, FoodItem] = field(default_factory=dict)  # texto original → alimento
    unrecognized: list[str] = field(default_factory=list)


def match_food_names(names: list[str], catalog: list[FoodItem]) -> MatchResult:
    by_exact = {f.name_es: f for f in catalog}
    by_norm = {normalize(f.name_es): f for f in catalog}
    # Los alias mandan sobre el fuzzy: "pollo" es pechuga, no muslo, porque el
    # catálogo lo dice, no porque un empate de SequenceMatcher caiga de un lado.
    by_alias = {normalize(a): f for f in catalog for a in f.aliases}
    result = MatchResult()

    for raw in names:
        text = raw.strip()
        if not text:
            continue
        norm = normalize(text)
        food = (
            by_exact.get(text) or by_norm.get(norm) or by_alias.get(norm) or _fuzzy(text, by_norm)
        )
        if food is not None:
            result.matched[raw] = food
        else:
            result.unrecognized.append(raw)
    return result


def _fuzzy(text: str, by_norm: dict[str, FoodItem]) -> FoodItem | None:
    """Mejor candidato por similitud; baja confianza → no reconocido.

    El desempate es por NOMBRE, no por orden de iteración. Antes ganaba el
    primero que superara al mejor, así que el mismo query resolvía a un alimento
    distinto según viniera el catálogo del CSV o de la base de datos — y el bono
    de palabra completa empata a 0.9 a todos los candidatos que contienen el
    término ("pollo" ∈ pechuga de pollo, muslo de pollo, pechuga de pollo
    desmechada). Los términos comunes y ambiguos se resuelven con `aliases`; el
    fuzzy solo tiene que ser reproducible.
    """
    query = normalize(text)
    if not query:
        return None
    scored: list[tuple[float, str, FoodItem]] = []
    for name, food in by_norm.items():
        score = SequenceMatcher(None, query, name).ratio()
        # bono si el query es subcadena por palabra completa ("pollo" ∈ "pechuga de pollo")
        if f" {query} " in f" {name} ":
            score = max(score, 0.9)
        scored.append((score, name, food))
    best_score, _name, best_food = max(scored, key=lambda t: (t[0], -len(t[1]), t[1]))
    return best_food if best_score >= FUZZY_THRESHOLD else None
