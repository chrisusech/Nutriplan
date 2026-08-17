"""La tira de 7 días del menú, anclada al día en que se genera.

El menú es una semana y el pesaje es semanal, así que todo lo que ocurre
"esta semana" se ancla al mismo `week_start`. No es el lunes ISO: quien se
registra un sábado tiene sábado–viernes, no dos días hasta el domingo.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

BOGOTA = ZoneInfo("America/Bogota")
_DAYS = 7


def today_bogota(when: date | datetime | None = None) -> date:
    """Hoy según el reloj de Bogotá, que es el que vive la persona."""
    if when is None:
        return datetime.now(tz=BOGOTA).date()
    if isinstance(when, datetime):
        if when.tzinfo is None:
            return when.replace(tzinfo=BOGOTA).date()
        return when.astimezone(BOGOTA).date()
    return when


def iso_week_start(when: date | datetime | None = None) -> date:
    """Lunes de la semana ISO que contiene `when`.

    Sigue vivo para métricas y hashes viejos. La identidad del menú del
    cliente es `today_bogota`, no este lunes.
    """
    if when is None:
        when = datetime.now(UTC).date()
    elif isinstance(when, datetime):
        when = when.astimezone(UTC).date() if when.tzinfo else when.date()
    return when - timedelta(days=when.weekday())


def today_weekday(today: date | None = None) -> int:
    """0 = lunes … 6 = domingo. El día de la semana del calendario, no del plan."""
    return (today or date.today()).weekday()


def plan_day_index(week_start: date, today: date | datetime | None = None) -> int | None:
    """Qué día de la tira es hoy (0..6), o None si ya se salió de ella."""
    delta = (today_bogota(today) - week_start).days
    if 0 <= delta < _DAYS:
        return delta
    return None


def plan_is_live(week_start: date, today: date | datetime | None = None) -> bool:
    """La tira todavía se está comiendo."""
    return plan_day_index(week_start, today) is not None


def next_week_start(week_start: date) -> date:
    return week_start + timedelta(days=_DAYS)


def client_week_start(week_start: date | None, today: date | datetime | None = None) -> date:
    """La semana del menú activo, o hoy si todavía no hay menú."""
    return week_start if week_start is not None else today_bogota(today)


def last_plan_day(week_start: date, today: date | datetime | None = None) -> int:
    """Qué chip abrir: hoy si está en la tira, el último si ya venció."""
    idx = plan_day_index(week_start, today)
    return _DAYS - 1 if idx is None else idx
