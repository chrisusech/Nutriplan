"""Resumen de la semana que se cierra: kcal marcadas, veces fuera, veredicto.

Los números son del plan que la persona vivió. El copy es fijo: no se le pide
a un modelo que narre lo que ya está en las filas.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from nutriplan.domain.models import MacroTargets, PlanCycle
from nutriplan.domain.restaurant import is_eating_out

# Por debajo de esto no se afirma que falló el objetivo: marcó muy poco.
_PARTIAL = 0.30
# Banda del objetivo semanal: ±8 %, en el espíritu del anillo del día.
_BAND = 0.08

Verdict = Literal["ok", "short", "over", "partial"]


def _fmt_kcal(n: int) -> str:
    return f"{n:,}".replace(",", ".")


@dataclass(frozen=True)
class WeekRecap:
    """Lo que se lee arriba del peso, al cerrar la semana."""

    kcal_eaten: int
    kcal_target: int
    meals_eaten: int
    meals_n: int
    eating_out_n: int
    verdict: Verdict

    @property
    def kcal_line(self) -> str:
        return (
            f"{_fmt_kcal(self.kcal_eaten)} kcal esta semana"
            f" · objetivo {_fmt_kcal(self.kcal_target)}"
        )

    @property
    def meals_line(self) -> str:
        return f"Marcaste {self.meals_eaten} de {self.meals_n} comidas"

    @property
    def out_line(self) -> str:
        if self.eating_out_n <= 0:
            return "No comiste fuera"
        if self.eating_out_n == 1:
            return "Comiste fuera 1 vez"
        return f"Comiste fuera {self.eating_out_n} veces"

    @property
    def verdict_line(self) -> str:
        if self.verdict == "partial":
            return f"Marcaste {self.meals_eaten} de {self.meals_n}. El recuento de kcal es parcial."
        if self.verdict == "ok":
            return "Estuviste en tu objetivo."
        days = 7
        if self.verdict == "short":
            gap = max(0, self.kcal_target - self.kcal_eaten)
            return f"Te quedaste corto (~{gap // days} kcal al día)."
        extra = max(0, self.kcal_eaten - self.kcal_target)
        return f"Te pasaste (~{extra // days} kcal al día)."


def week_recap(plan: PlanCycle, daily: MacroTargets) -> WeekRecap:
    """Suma lo marcado, cuenta la calle y decide el veredicto del objetivo."""
    meals = [m for day in plan.days for m in day.meals]
    meals_n = len(meals)
    eaten = [m for m in meals if m.eaten]
    kcal_eaten = round(sum(m.computed.kcal for m in eaten))
    kcal_target = round(daily.kcal) * 7
    out_n = sum(1 for m in meals if is_eating_out(m))
    marked = (len(eaten) / meals_n) if meals_n else 0.0
    if meals_n == 0 or marked < _PARTIAL:
        verdict: Verdict = "partial"
    else:
        ratio = kcal_eaten / kcal_target if kcal_target else 0.0
        if abs(ratio - 1.0) <= _BAND:
            verdict = "ok"
        elif ratio < 1.0:
            verdict = "short"
        else:
            verdict = "over"
    return WeekRecap(
        kcal_eaten=kcal_eaten,
        kcal_target=kcal_target,
        meals_eaten=len(eaten),
        meals_n=meals_n,
        eating_out_n=out_n,
        verdict=verdict,
    )
