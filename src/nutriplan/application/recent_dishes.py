"""Lo que comió la semana pasada: para no servirle el mismo caldo otra vez."""

from __future__ import annotations

from datetime import date

from nutriplan.domain.models import PlanCycle


def dishes_of_previous_week(
    plans: list[PlanCycle], week: date
) -> tuple[frozenset[str], frozenset[str]]:
    """`dish_key` y `template_id` del plan más reciente anterior a `week`."""
    prior = [p for p in plans if p.week_start < week]
    if not prior:
        return frozenset(), frozenset()
    last = max(prior, key=lambda p: (p.week_start, p.variant))
    keys = frozenset(m.dish_key for d in last.days for m in d.meals if m.dish_key)
    templates = frozenset(t for d in last.days for m in d.meals if (t := m.template_id))
    return keys, templates
