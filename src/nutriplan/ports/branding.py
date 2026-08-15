"""Puerto del branding de un tenant."""

from typing import Protocol

from nutriplan.domain.models import Branding


class BrandingStore(Protocol):
    """Dónde queda guardada la marca de una cuenta recién creada.

    El alta necesita dejarla escrita, pero no tiene por qué saber que hoy es un
    YAML en disco: mañana es una fila o un bucket.
    """

    def save(self, branding: Branding, *, tenant: str) -> None: ...
