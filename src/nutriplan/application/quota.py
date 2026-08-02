"""La cuota del BETA: un menú, y el segundo se gana.

Regla acordada: cada persona genera un menú. Para desbloquear el siguiente
tiene que calificar sus platos y dejar un comentario — que es exactamente el
dato por el que existe este lanzamiento.
"""

from dataclasses import dataclass

# Con menos de esto no hay señal: cinco platos son un día entero de comidas.
RATINGS_REQUIRED = 5


@dataclass(frozen=True)
class MenuQuota:
    """Si puede generar otro menú, y qué le falta si no."""

    unlocked: bool
    ratings: int
    has_feedback: bool

    @property
    def hint(self) -> str:
        if self.unlocked:
            return ""
        faltan = max(RATINGS_REQUIRED - self.ratings, 0)
        if faltan and not self.has_feedback:
            return (
                f"Califica {faltan} plato(s) más y cuéntanos qué te pareció "
                "para desbloquear otra semana."
            )
        if faltan:
            return f"Te faltan {faltan} plato(s) por calificar para otra semana."
        return "Déjanos un comentario en Opinar y te desbloqueamos otra semana."


def evaluate(
    *, menus_generated: int, ratings: int, has_feedback: bool, max_menus: int | None
) -> MenuQuota:
    """`max_menus` es el override del super_user: None = regla del BETA."""
    if max_menus is None:
        earned = ratings >= RATINGS_REQUIRED and has_feedback
        allowed = 2 if earned else 1
    else:
        allowed = max_menus
        earned = True
    return MenuQuota(
        unlocked=menus_generated < allowed,
        ratings=ratings,
        has_feedback=has_feedback,
    )
