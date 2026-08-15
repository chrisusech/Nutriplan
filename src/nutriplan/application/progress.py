"""El historial de alguien, semana a semana.

Ahora que los planes ya no se borran, hay algo que enseñar: qué pesaba, con
cuántas kcal, qué menú vivió y qué le pareció. Es la prueba visible de que el
seguimiento existe — y para quien está bajando de peso, la razón de volver.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from nutriplan.domain.models import MacroTargets, NutritionTargets, PlanCycle, WeightEntry
from nutriplan.domain.week_recap import week_recap

# Un peso puede reutilizar los macros de la última vez que se pesó igual.
_WEIGHT_TOL_KG = 0.05


@dataclass(frozen=True)
class WeekProgress:
    """Una fila del historial: la semana como la vivió la persona."""

    week_start: date
    weight_kg: float
    delta_kg: float | None
    kcal: int | None
    comment: str | None
    note: str | None
    plan_id: UUID | None
    dishes: int
    rating_avg: float | None
    rating_count: int
    kcal_eaten: int | None = None
    kcal_target: int | None = None

    @property
    def is_gain(self) -> bool:
        return self.delta_kg is not None and self.delta_kg > 0


def build_progress(
    *,
    entries: list[WeightEntry],
    targets: list[NutritionTargets],
    plans: list[PlanCycle],
    ratings: dict[UUID, tuple[float, int]] | None = None,
) -> list[WeekProgress]:
    """Cruza pesajes, macros y planes por semana ISO. De la más nueva a la vieja.

    Las kcal de una semana son las que se calcularon con ese peso: emparejar por
    fecha fallaba porque el check-in guarda peso y macros en el mismo segundo, y
    reenviar el cierre reescribe los macros sin mover la semana.
    """
    ratings = ratings or {}
    by_week: dict[date, PlanCycle] = {}
    for plan in sorted(plans, key=lambda p: (p.week_start, p.variant)):
        by_week[plan.week_start] = plan

    ordered = sorted(entries, key=lambda e: e.week_start)
    rows: list[WeekProgress] = []
    for i, entry in enumerate(ordered):
        previous = ordered[i - 1].weight_kg if i else None
        week_plan = by_week.get(entry.week_start)
        avg, count = ratings.get(week_plan.id, (None, 0)) if week_plan else (None, 0)
        daily_kcal = _kcal_for(entry.weight_kg, targets)
        eaten = None
        weekly_target = None
        if week_plan is not None and daily_kcal is not None:
            recap = week_recap(
                week_plan,
                MacroTargets(kcal=daily_kcal, protein_g=0, carb_g=0, fat_g=0),
            )
            eaten = recap.kcal_eaten
            weekly_target = recap.kcal_target
        rows.append(
            WeekProgress(
                week_start=entry.week_start,
                weight_kg=entry.weight_kg,
                delta_kg=(round(entry.weight_kg - previous, 1) if previous is not None else None),
                kcal=daily_kcal,
                comment=entry.client_comment,
                note=entry.note,
                plan_id=week_plan.id if week_plan else None,
                dishes=sum(len(d.meals) for d in week_plan.days) if week_plan else 0,
                rating_avg=avg,
                rating_count=count,
                kcal_eaten=eaten,
                kcal_target=weekly_target,
            )
        )
    return list(reversed(rows))


_DAY_SHORT = ("L", "M", "X", "J", "V", "S", "D")


@dataclass(frozen=True)
class DayKcal:
    """Kcal marcadas de un día frente al objetivo. Para la gráfica de la semana."""

    label: str
    kcal: int
    objetivo: int


def daily_kcal(plan: PlanCycle, daily: MacroTargets) -> list[DayKcal]:
    """Siete barras: lo marcado cada día contra el objetivo diario."""
    objetivo = round(daily.kcal)
    return [
        DayKcal(
            label=_DAY_SHORT[day.day_index],
            kcal=round(sum(m.computed.kcal for m in day.meals if m.eaten)),
            objetivo=objetivo,
        )
        for day in sorted(plan.days, key=lambda d: d.day_index)
    ]


def _kcal_for(weight_kg: float, targets: list[NutritionTargets]) -> int | None:
    matching = [
        t
        for t in targets
        if t.weight_kg is not None and abs(t.weight_kg - weight_kg) <= _WEIGHT_TOL_KG
    ]
    if not matching:
        return None
    return int(round(max(matching, key=lambda t: t.computed_at).daily.kcal))
