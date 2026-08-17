"""Cuándo toca armar la semana siguiente, y de qué tira.

El último día de LA tira de esa persona (week_start + 6) desde las 18 h no
puede rehacer el menú que todavía se come: el objetivo es el día 8. A la
mañana siguiente se recoge a quien cerró tarde.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from nutriplan.domain.week import next_week_start, today_bogota

BOGOTA = ZoneInfo("America/Bogota")
_LAST_DAY_FROM = time(18, 0)
_NEXT_DAY_FROM = time(8, 0)
_NEXT_DAY_UNTIL = time(12, 0)


def now_bogota(when: datetime | None = None) -> datetime:
    moment = when or datetime.now(tz=BOGOTA)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=BOGOTA)
    return moment.astimezone(BOGOTA)


def in_auto_window(week_start: date, when: datetime | None = None) -> bool:
    """Día 7 desde las 18 h, o día 8 8–12 (quien cerró tarde)."""
    local = now_bogota(when)
    today = local.date()
    clock = local.timetz().replace(tzinfo=None)
    last_day = week_start + timedelta(days=6)
    next_start = next_week_start(week_start)
    if today == last_day:
        return clock >= _LAST_DAY_FROM
    if today == next_start:
        return _NEXT_DAY_FROM <= clock < _NEXT_DAY_UNTIL
    return False


def should_activate(target_week: date, when: datetime | None = None) -> bool:
    """El menú que empieza mañana no se activa la tarde del día 7."""
    return today_bogota(when) >= target_week


def closed_and_target_weeks(week_start: date) -> tuple[date, date]:
    """Semana que se cierra y primer día del menú que hay que crear."""
    return week_start, next_week_start(week_start)
