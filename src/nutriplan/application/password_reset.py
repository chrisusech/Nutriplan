"""Recuperación de contraseña en local: token en pantalla, sin correo."""

from dataclasses import dataclass

import structlog

from nutriplan.adapters.auth import hash_password
from nutriplan.adapters.password_reset_token import issue_token, verify_token
from nutriplan.application.auth import AuthRepository, SignupError

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class PasswordResetLink:
    email: str
    url_path: str  # p. ej. /recuperar/eyJhYmMi...
    expires_minutes: int


async def request_password_reset(
    *,
    email: str,
    auth_repo: AuthRepository,
    session_secret: str,
    ttl_seconds: int = 3600,
) -> PasswordResetLink | None:
    """Genera enlace si el correo existe; None si no hay cuenta."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise SignupError("Correo inválido")
    if await auth_repo.get_by_email(email) is None:
        return None
    token = issue_token(email=email, secret=session_secret, ttl_seconds=ttl_seconds)
    logger.info("password_reset_requested", email=email)
    return PasswordResetLink(
        email=email,
        url_path=f"/recuperar/{token}",
        expires_minutes=ttl_seconds // 60,
    )


async def complete_password_reset(
    *,
    token: str,
    new_password: str,
    auth_repo: AuthRepository,
    session_secret: str,
) -> str:
    """Aplica la nueva contraseña. Devuelve el correo actualizado."""
    if len(new_password) < 8:
        raise SignupError("La contraseña debe tener al menos 8 caracteres")
    email = verify_token(token, secret=session_secret)
    if email is None:
        raise SignupError("El enlace expiró o no es válido. Pide uno nuevo.")
    if await auth_repo.get_by_email(email) is None:
        raise SignupError("La cuenta ya no existe.")
    updated = await auth_repo.set_password_hash(email, hash_password(new_password))
    if not updated:
        raise SignupError("No se pudo actualizar la contraseña.")
    logger.info("password_reset_completed", email=email)
    return email
