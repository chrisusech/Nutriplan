"""Racha de días completos: todas las comidas del día marcadas.

No hay tabla nueva. Se lee de los `eaten` que ya viven en el plan. Si hoy
todavía no se cerró, la racha cuenta desde ayer — como Duolingo, no se rompe
a las 9 de la mañana.
"""

from __future__ import annotations

from datetime import date, timedelta

from nutriplan.domain.models import DayPlan, PlanCycle


def day_is_complete(day: DayPlan) -> bool:
    """True si el día tiene comidas y todas están marcadas."""
    return bool(day.meals) and all(m.eaten for m in day.meals)


def completed_dates(plans: list[PlanCycle]) -> set[date]:
    """Los días del calendario que se cerraron enteros."""
    out: set[date] = set()
    for plan in plans:
        for day in plan.days:
            if day_is_complete(day):
                out.add(plan.week_start + timedelta(days=day.day_index))
    return out


def current_streak(plans: list[PlanCycle], *, today: date | None = None) -> int:
    """Días seguidos completos hasta hoy (o hasta ayer si hoy sigue abierto)."""
    done = completed_dates(plans)
    if not done:
        return 0
    cursor = today or date.today()
    if cursor not in done:
        cursor -= timedelta(days=1)
    n = 0
    while cursor in done:
        n += 1
        cursor -= timedelta(days=1)
    return n
