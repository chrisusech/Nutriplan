"""Puerto de envío de correo.

Lo mínimo que la app necesita: verificar una dirección y recuperar una clave.
Nada de plantillas ni de campañas — eso no es problema del dominio.
"""

from typing import Protocol


class EmailSender(Protocol):
    async def send(self, *, to: str, subject: str, body: str) -> None:
        """Envía un correo de texto plano. Falla ruidosamente si no puede."""
        ...
