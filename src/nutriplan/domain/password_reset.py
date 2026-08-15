"""El enlace para volver a entrar cuando alguien olvidó su contraseña.

Un token firmado y sin estado no se puede retirar: mientras no venza sirve, y
sirve tantas veces como se use. Para esto no vale — el enlace viaja por correo,
que es el sitio menos privado de todos, y una vez usado tiene que morir. Así que
el token es un secreto aleatorio del que solo se guarda el hash, igual que una
contraseña, y su fila es la que dice si ya se gastó.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

# Una hora: suficiente para ir al correo, corto para un enlace que abre una
# cuenta.
RESET_TTL_SECONDS = 3600


class PasswordResetToken(BaseModel):
    """Lo que se guarda de un enlace. El token en claro nunca está aquí."""

    model_config = ConfigDict(frozen=True)

    tenant_id: UUID
    user_id: UUID
    token_hash: str
    expires_at: datetime


def new_reset_token() -> str:
    """El secreto que viaja en el correo. Solo existe en ese mensaje."""
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    """Lo que sí se guarda. Un volcado de la tabla no da enlaces usables."""
    return hashlib.sha256(token.encode()).hexdigest()
