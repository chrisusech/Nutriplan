"""El gusto de una persona, deducido de lo que ha calificado.

Calificar platos servía solo para abrir cuota: el dato se guardaba y se moría
ahí. Aquí se convierte en lo que hace que la semana 6 se parezca más a quien la
come que la semana 1.

Todo esto es aritmética sobre notas: sin I/O, sin IA y sin sorpresas. La IA solo
entra después, para leer los comentarios en prosa (`application/taste_profile`).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Cinco estrellas: 4 o 5 es "repítemelo", 1 o 2 es "no me lo pongas más".
LOVED_FROM = 4
REJECTED_UPTO = 2
# Con una sola nota no se veta nada: pudo ser un mal día, no el plato.
MIN_VOTES_TO_REJECT = 2
# Cuánto cabe en el prompt sin desplazar al catálogo.
MAX_ITEMS_IN_PROMPT = 8


@dataclass(frozen=True)
class RatedDish:
    """Una calificación, aplanada. Lo que el repositorio sabe devolver."""

    template_id: str | None
    dish_key: str | None
    dish_name: str | None
    rating: int
    comment: str | None = None


@dataclass
class _Tally:
    votes: int = 0
    total: int = 0
    names: set[str] = field(default_factory=set)

    @property
    def average(self) -> float:
        return self.total / self.votes if self.votes else 0.0


class TasteProfile(BaseModel):
    """Lo que sabemos del gusto de alguien, listo para entrar en el prompt."""

    model_config = ConfigDict(frozen=True)

    loved_dishes: list[str] = Field(default_factory=list)
    rejected_dishes: list[str] = Field(default_factory=list)
    loved_keys: list[str] = Field(default_factory=list)
    rejected_keys: list[str] = Field(default_factory=list)
    loved_templates: list[str] = Field(default_factory=list)
    rejected_templates: list[str] = Field(default_factory=list)
    # Ids de alimentos que la IA interpretó de los comentarios de la persona.
    avoid_food_ids: list[UUID] = Field(default_factory=list)
    prefer_food_ids: list[UUID] = Field(default_factory=list)
    # Peticiones en prosa que no son un alimento ("menos fritos", "más variedad").
    adjustments: list[str] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (
            self.loved_dishes
            or self.rejected_dishes
            or self.loved_keys
            or self.rejected_keys
            or self.loved_templates
            or self.rejected_templates
            or self.avoid_food_ids
            or self.prefer_food_ids
            or self.adjustments
        )

    def fingerprint(self) -> str:
        """Resumen estable para el `input_hash`.

        Sin esto, alguien que dijo "no más pescado" recibiría de la caché el
        mismo menú con pescado: el perfil no cambió, los macros tampoco, y el
        hash sería idéntico.
        """
        parts = [
            ",".join(sorted(self.rejected_dishes)),
            ",".join(sorted(self.loved_dishes)),
            ",".join(sorted(self.rejected_keys)),
            ",".join(sorted(self.loved_keys)),
            ",".join(sorted(self.rejected_templates)),
            ",".join(sorted(self.loved_templates)),
            ",".join(sorted(str(f) for f in self.avoid_food_ids)),
            ",".join(sorted(str(f) for f in self.prefer_food_ids)),
            ",".join(sorted(self.adjustments)),
        ]
        return "|".join(parts)


def build_taste_profile(
    ratings: list[RatedDish],
    *,
    avoid_food_ids: list[UUID] | None = None,
    prefer_food_ids: list[UUID] | None = None,
    adjustments: list[str] | None = None,
) -> TasteProfile:
    """Agrega las notas por `dish_key`: el plato exacto que se comió."""
    by_dish: dict[str, _Tally] = defaultdict(_Tally)

    for r in ratings:
        if not r.dish_key:
            continue
        tally = by_dish[r.dish_key]
        tally.votes += 1
        tally.total += r.rating
        if r.dish_name:
            tally.names.add(r.dish_name)

    loved = _selected(by_dish, loved=True)
    rejected = _selected(by_dish, loved=False)
    return TasteProfile(
        loved_dishes=_names_of(loved),
        rejected_dishes=_names_of(rejected),
        loved_keys=[k for k, _ in loved],
        rejected_keys=[k for k, _ in rejected],
        loved_templates=_templates(ratings, {k for k, _ in loved}),
        rejected_templates=_templates(ratings, {k for k, _ in rejected}),
        avoid_food_ids=list(avoid_food_ids or []),
        prefer_food_ids=list(prefer_food_ids or []),
        adjustments=list(adjustments or []),
    )


def _selected(tallies: dict[str, _Tally], *, loved: bool) -> list[tuple[str, _Tally]]:
    if loved:
        picked = [(k, t) for k, t in tallies.items() if t.average >= LOVED_FROM]
        picked.sort(key=lambda kv: (-kv[1].average, -kv[1].votes))
    else:
        picked = [
            (k, t)
            for k, t in tallies.items()
            if t.average <= REJECTED_UPTO and t.votes >= MIN_VOTES_TO_REJECT
        ]
        picked.sort(key=lambda kv: (kv[1].average, -kv[1].votes))
    return picked[:MAX_ITEMS_IN_PROMPT]


def _names_of(picked: list[tuple[str, _Tally]]) -> list[str]:
    out: list[str] = []
    for _, tally in picked:
        out.extend(sorted(tally.names))
    return list(dict.fromkeys(out))[:MAX_ITEMS_IN_PROMPT]


def _templates(ratings: list[RatedDish], keys: set[str]) -> list[str]:
    seen: list[str] = []
    for r in ratings:
        if r.dish_key in keys and r.template_id and r.template_id not in seen:
            seen.append(r.template_id)
    return seen[:MAX_ITEMS_IN_PROMPT]


def _names(tallies: dict[str, _Tally], *, loved: bool) -> list[str]:
    """El nombre del plato es lo único que la IA entiende; la clave no le dice nada."""
    return _names_of(_selected(tallies, loved=loved))
