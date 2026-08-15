"""Qué significa "cerrar la semana", y qué falta cuando no está cerrada.

Antes de la semana siguiente pedimos el peso y cinco platos con estrella. El
peso adapta las kcal; las notas alimentan el motor. El comentario es bienvenido
y la IA lo lee — pero no es puerta: nadie se queda sin menú por no escribir.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_COMMENT_CHARS = 10
RATINGS_REQUIRED = 5


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
        return self.has_weight and self.ratings_done

    @property
    def progress(self) -> int:
        """Porcentaje del cierre, para la barra de la pantalla."""
        if self.is_first_week:
            return 100 if self.has_weight else 0
        done = 0.0
        done += 1.0 if self.has_weight else 0.0
        done += 1.0 if self.ratings_done else 0.0
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
        if not self.ratings_done:
            pending.append(f"calificar al menos {self.ratings_required} platos")
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
