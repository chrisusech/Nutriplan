"""Lo que todos los repositorios comparten.

Cada uno recibe `(session, tenant_id)` y filtra por tenant en CADA consulta.
No hay filtro central: si una consulta nueva se olvida del `where`, se filtra
sola. Los tests de aislamiento son la red que lo detecta.
"""

from datetime import UTC, datetime


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes ingenuos; el dominio los quiere con zona."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
