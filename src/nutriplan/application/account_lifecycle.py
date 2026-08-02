"""Verificar el correo de una cuenta y borrarla cuando lo pidan.

El borrado no es opcional: Apple lo exige (5.1.1(v)) para publicar una app que
permite registrarse. Y como el BETA existe para recolectar datos, hay que poder
decir exactamente qué se borra y qué queda.
"""

from typing import Protocol
from uuid import UUID

import structlog

from nutriplan.adapters.password_reset_token import issue_token, verify_token
from nutriplan.application.auth import AuthRepository, SignupError
from nutriplan.ports.email_sender import EmailSender

logger = structlog.get_logger(__name__)

VERIFY_TTL_S = 48 * 3600


class AccountEraser(Protocol):
    async def purge_tenant(self, tenant_id: UUID) -> None: ...
    async def anonymize_events(self, user_id: UUID) -> None: ...
    async def mark_deleted(self, user_id: UUID) -> None: ...


async def send_verification_email(
    *, email: str, name: str, base_url: str, secret: str, mailer: EmailSender
) -> None:
    token = issue_token(email=email, secret=secret, ttl_seconds=VERIFY_TTL_S)
    link = f"{base_url.rstrip('/')}/verificar/{token}"
    await mailer.send(
        to=email,
        subject="Confirma tu correo · NutriPlan",
        body=(
            f"Hola {name or ''},\n\n"
            f"Confirma tu correo para guardar tu menú:\n{link}\n\n"
            f"El enlace vence en {VERIFY_TTL_S // 3600} horas.\n"
            "Si no fuiste tú, ignora este mensaje."
        ),
    )
    logger.info("verification_email_sent", email=email)


async def confirm_email(
    *, token: str, secret: str, auth_repo: AuthRepository
) -> str:
    """Marca el correo como verificado. Devuelve la dirección."""
    email = verify_token(token, secret=secret)
    if email is None:
        raise SignupError("El enlace expiró o no es válido. Pide uno nuevo.")
    account = await auth_repo.get_by_email_any_provider(email)
    if account is None:
        raise SignupError("La cuenta ya no existe.")
    await auth_repo.mark_email_verified(account.id)
    logger.info("email_verified", user_id=str(account.id))
    return email


async def delete_account(
    *, user_id: UUID, tenant_id: UUID, eraser: AccountEraser
) -> None:
    """Borra la cuenta y todo lo que la identifica.

    Los eventos de analítica NO se borran: se desligan de la persona. El embudo
    agregado es el producto del BETA, y una vez sin `user_id` ya no son suyos.
    Lo demás —perfil, menús, ratings, feedback— se va entero.
    """
    await eraser.purge_tenant(tenant_id)
    await eraser.anonymize_events(user_id)
    await eraser.mark_deleted(user_id)
    logger.info("account_deleted", user_id=str(user_id), tenant_id=str(tenant_id))


def deletion_receipt() -> str:
    """Lo que la app le promete a quien borra su cuenta."""
    return (
        "Se borraron tu perfil, tus menús, tus calificaciones y tus comentarios. "
        "Las estadísticas de uso se conservan sin ningún dato que te identifique."
    )
