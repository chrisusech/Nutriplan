"""Lo que todos los repositorios comparten.

Cada uno recibe `(session, tenant_id)` y filtra por tenant en CADA consulta.
No hay filtro central: si una consulta nueva se olvida del `where`, se filtra
sola. Los tests de aislamiento son la red que lo detecta.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from nutriplan.adapters.db.models import UserRow
from nutriplan.domain.models import Role


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes ingenuos; el dominio los quiere con zona."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def not_lab(column: Any) -> Any:
    """Fuera los tenants del super_user: probar la app no es usarla.

    Sin esto, cada menú que se genera desde el laboratorio aparece como una
    persona más en el embudo y en el análisis, y contamina justo las cifras que
    se miran para decidir qué arreglar.
    """
    return column.not_in(select(UserRow.tenant_id).where(UserRow.role == Role.SUPER_USER.value))


def seudonimo(client_id: Any) -> str:
    """Los ocho primeros caracteres del id: bastan para seguir a la misma
    persona por sus semanas en un análisis, y no dicen quién es fuera de la
    base."""
    return str(client_id)[:8]
