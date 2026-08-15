"""Cuándo toca armar la semana siguiente, y de qué lunes.

El domingo sigue siendo ESTA semana ISO: generar a las 18 h no puede rehacer
el menú que la persona todavía está comiendo. El objetivo es el lunes que
viene; el lunes por la mañana se recoge a quien cerró tarde.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")
SUNDAY = 6
MONDAY = 0
_SUNDAY_FROM = time(18, 0)
_MONDAY_FROM = time(8, 0)
_MONDAY_UNTIL = time(12, 0)


def now_bogota(when: datetime | None = None) -> datetime:
    moment = when or datetime.now(tz=BOGOTA)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=BOGOTA)
    return moment.astimezone(BOGOTA)


def in_auto_window(when: datetime | None = None) -> bool:
    """Domingo desde las 18 h, o lunes 8–12 (quien cerró tarde)."""
    local = now_bogota(when)
    clock = local.timetz().replace(tzinfo=None)
    if local.weekday() == SUNDAY:
        return clock >= _SUNDAY_FROM
    if local.weekday() == MONDAY:
        return _MONDAY_FROM <= clock < _MONDAY_UNTIL
    return False


def this_monday(when: datetime | None = None) -> date:
    """Lunes ISO según el reloj de Bogotá, no el de UTC."""
    local = now_bogota(when).date()
    return local - timedelta(days=local.weekday())


def should_activate(target_week: date, when: datetime | None = None) -> bool:
    """El menú del lunes que viene no se activa el domingo (19 h Bogotá = lunes UTC)."""
    return target_week <= this_monday(when)


def closed_and_target_weeks(when: datetime | None = None) -> tuple[date, date]:
    """Semana que se cierra y lunes del menú que hay que crear."""
    monday = this_monday(when)
    if now_bogota(when).weekday() == SUNDAY:
        return monday, monday + timedelta(days=7)
    return monday - timedelta(days=7), monday
