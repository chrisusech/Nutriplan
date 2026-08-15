"""Recuperación de contraseña por correo.

El enlace es de un solo uso y deja rastro: se emite una fila, se consume esa
fila, y al terminar se tiran las que quedaran pendientes. Un enlace que se
filtre después de usarse ya no abre nada.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import structlog

from nutriplan.application.auth import AuthRepository, SignupError
from nutriplan.domain.password_reset import (
    RESET_TTL_SECONDS,
    PasswordResetToken,
    new_reset_token,
    token_fingerprint,
)
from nutriplan.domain.passwords import hash_password

logger = structlog.get_logger(__name__)


class PasswordResetRepository(Protocol):
    async def add(self, token: PasswordResetToken) -> None: ...
    async def peek(self, token_hash: str, *, now: datetime) -> UUID | None: ...
    async def consume(self, token_hash: str, *, now: datetime) -> UUID | None: ...
    async def invalidate_for_user(self, user_id: UUID) -> None: ...


@dataclass(frozen=True)
class PasswordResetLink:
    email: str
    url_path: str  # p. ej. /recuperar/eyJhYmMi...
    expires_minutes: int


async def request_password_reset(
    *,
    email: str,
    auth_repo: AuthRepository,
    tokens: PasswordResetRepository,
    ttl_seconds: int = RESET_TTL_SECONDS,
    now: datetime | None = None,
) -> PasswordResetLink | None:
    """Genera enlace si el correo existe; None si no hay cuenta."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise SignupError("Correo inválido")
    # get_by_email exige hash: quien entró con Google no tiene contraseña que
    # recuperar, y pedirlo no debe decir si la cuenta existe.
    fila = await auth_repo.get_by_email(email)
    if fila is None:
        return None
    account, _ = fila

    # Pedir uno nuevo retira el anterior: si no, quien pide dos enlaces se queda
    # con dos llaves vivas y solo se acuerda de una.
    await tokens.invalidate_for_user(account.id)
    moment = now or datetime.now(UTC)
    raw = new_reset_token()
    await tokens.add(
        PasswordResetToken(
            tenant_id=account.tenant_id,
            user_id=account.id,
            token_hash=token_fingerprint(raw),
            expires_at=moment + timedelta(seconds=ttl_seconds),
        )
    )
    logger.info("password_reset_requested", user_id=str(account.id))
    return PasswordResetLink(
        email=email,
        url_path=f"/recuperar/{raw}",
        expires_minutes=ttl_seconds // 60,
    )


async def email_behind_reset_link(
    *,
    token: str,
    auth_repo: AuthRepository,
    tokens: PasswordResetRepository,
    now: datetime | None = None,
) -> str | None:
    """De quién es el enlace, para pintar la pantalla. No lo gasta."""
    user_id = await tokens.peek(token_fingerprint(token), now=now or datetime.now(UTC))
    if user_id is None:
        return None
    account = await auth_repo.get_by_id(user_id)
    return account.email if account is not None else None


async def complete_password_reset(
    *,
    token: str,
    new_password: str,
    auth_repo: AuthRepository,
    tokens: PasswordResetRepository,
    now: datetime | None = None,
) -> str:
    """Aplica la nueva contraseña. Devuelve el correo actualizado."""
    if len(new_password) < 8:
        raise SignupError("La contraseña debe tener al menos 8 caracteres")
    user_id = await tokens.consume(token_fingerprint(token), now=now or datetime.now(UTC))
    if user_id is None:
        raise SignupError("El enlace expiró o no es válido. Pide uno nuevo.")
    account = await auth_repo.get_by_id(user_id)
    if account is None or not account.email:
        raise SignupError("El enlace expiró o no es válido. Pide uno nuevo.")
    updated = await auth_repo.set_password_hash(account.email, hash_password(new_password))
    if not updated:
        raise SignupError("No se pudo actualizar la contraseña.")
    # Lo pendiente ya no vale: cambiar la clave cierra todas las puertas abiertas.
    await tokens.invalidate_for_user(user_id)
    logger.info("password_reset_completed", user_id=str(user_id))
    return account.email
