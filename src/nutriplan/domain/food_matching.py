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
    result = MatchResult()

    for raw in names:
        text = raw.strip()
        if not text:
            continue
        food = by_exact.get(text) or by_norm.get(normalize(text)) or _fuzzy(text, by_norm)
        if food is not None:
            result.matched[raw] = food
        else:
            result.unrecognized.append(raw)
    return result


def _fuzzy(text: str, by_norm: dict[str, FoodItem]) -> FoodItem | None:
    """Mejor candidato por similitud; empates o baja confianza → no reconocido."""
    query = normalize(text)
    best_score, best_food = 0.0, None
    for name, food in by_norm.items():
        score = SequenceMatcher(None, query, name).ratio()
        # bono si el query es subcadena por palabra completa ("pollo" ∈ "pechuga de pollo")
        if query and (f" {query} " in f" {name} "):
            score = max(score, 0.9)
        if score > best_score:
            best_score, best_food = score, food
    return best_food if best_score >= FUZZY_THRESHOLD else None
