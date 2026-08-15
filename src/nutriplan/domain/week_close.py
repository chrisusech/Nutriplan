"""Qué significa "cerrar la semana", y qué falta cuando no está cerrada.

Antes de la semana siguiente pedimos el peso y una frase sobre cómo fue. El
peso adapta las kcal; el comentario es lo que la IA traduce a gustos. Calificar
platos sigue existiendo — alimenta el motor y las métricas — pero ya no es
puerta: pedir diez notas era fricción de beta.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_COMMENT_CHARS = 10


@dataclass(frozen=True)
class WeekClosure:
    """El estado del cierre de una semana concreta."""

    has_weight: bool
    ratings: int
    ratings_required: int
    has_comment: bool
    # La primera semana no tiene nada que cerrar: no hubo plan que calificar.
    is_first_week: bool = False

    @property
    def ratings_done(self) -> bool:
        return self.ratings_required <= 0 or self.ratings >= self.ratings_required

    @property
    def is_closed(self) -> bool:
        if self.is_first_week:
            return self.has_weight
        return self.has_weight and self.has_comment

    @property
    def progress(self) -> int:
        """Porcentaje del cierre, para la barra de la pantalla."""
        if self.is_first_week:
            return 100 if self.has_weight else 0
        done = 0.0
        done += 1.0 if self.has_weight else 0.0
        done += 1.0 if self.has_comment else 0.0
        return int(round(done / 2 * 100))

    @property
    def progress_bucket(self) -> int:
        """El avance en decenas (0-10): la CSP no deja pintar el ancho en línea."""
        return round(self.progress / 10)

    @property
    def missing(self) -> list[str]:
        """Lo que falta, en el orden en que se le pide a la persona."""
        if self.is_first_week:
            return [] if self.has_weight else ["registrar tu peso de esta semana"]
        pending: list[str] = []
        if not self.has_weight:
            pending.append("registrar tu peso de esta semana")
        if not self.has_comment:
            pending.append("contarnos cómo te fue")
        return pending

    @property
    def hint(self) -> str:
        pending = self.missing
        if not pending:
            return ""
        if len(pending) == 1:
            return f"Para tu semana siguiente te falta {pending[0]}."
        return "Para tu semana siguiente te falta: " + "; ".join(pending) + "."


def is_valid_comment(text: str | None) -> bool:
    """Una frase de verdad, no un espacio para saltarse la pantalla."""
    return bool(text and len(text.strip()) >= MIN_COMMENT_CHARS)
