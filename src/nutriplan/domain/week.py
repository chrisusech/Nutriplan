"""La semana ISO: la unidad de tiempo del producto.

El menú es una semana y el pesaje es semanal, así que todo lo que ocurre
"esta semana" se ancla al mismo lunes. Tener el cálculo en un solo sitio del
dominio evita que el plan crea estar en una semana y el check-in en otra.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta


def iso_week_start(when: date | datetime | None = None) -> date:
    """Lunes de la semana ISO que contiene `when` (hoy en UTC si es None)."""
    if when is None:
        when = datetime.now(UTC).date()
    elif isinstance(when, datetime):
        when = when.astimezone(UTC).date() if when.tzinfo else when.date()
    return when - timedelta(days=when.weekday())


def today_weekday(today: date | None = None) -> int:
    """0 = lunes … 6 = domingo. El día que la persona vive, no el lunes del plan."""
    return (today or date.today()).weekday()
