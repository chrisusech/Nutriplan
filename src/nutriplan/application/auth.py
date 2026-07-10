"""Casos de uso de autenticación: alta y login de entrenadores.

Cada entrenador es dueño de su tenant; el aislamiento por tenant (regla de oro)
ya lo garantizan los repos. Aquí solo se crea la cuenta y se verifica la clave.
"""

from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import structlog

from nutriplan.adapters.auth import hash_password, verify_password
from nutriplan.adapters.branding_store import save_branding
from nutriplan.domain.models import Branding, Trainer

logger = structlog.get_logger(__name__)


class AuthRepository(Protocol):
    async def get_by_email(self, email: str) -> tuple[Trainer, str] | None: ...
    async def create_account(
        self, *, tenant_id: UUID, tenant_name: str, name: str,
        email: str, password_hash: str, role: str = "trainer",
    ) -> Trainer: ...
    async def create_client_login(
        self, *, tenant_id: UUID, client_id: UUID, name: str,
        email: str, password_hash: str,
    ) -> Trainer: ...


class SignupError(ValueError):
    """El alta no se pudo completar (correo duplicado, datos inválidos)."""


async def signup_trainer(
    *,
    name: str,
    email: str,
    password: str,
    business_name: str,
    auth_repo: AuthRepository,
    branding_dir: Path,
    admin_email: str = "",
) -> Trainer:
    email = email.strip().lower()
    if not email or "@" not in email:
        raise SignupError("Correo inválido")
    if len(password) < 8:
        raise SignupError("La contraseña debe tener al menos 8 caracteres")
    if await auth_repo.get_by_email(email) is not None:
        raise SignupError("Ese correo ya tiene una cuenta")

    # El admin de plataforma verifica recetas; se designa por correo en settings.
    role = "admin" if admin_email and email == admin_email.strip().lower() else "trainer"
    tenant_id = uuid4()
    trainer = await auth_repo.create_account(
        tenant_id=tenant_id, tenant_name=business_name.strip() or name.strip(),
        name=name.strip(), email=email, password_hash=hash_password(password), role=role,
    )
    save_branding(
        branding_dir, Branding(tenant_name=business_name.strip() or name.strip()),
        tenant=str(tenant_id),
    )
    logger.info("trainer_signed_up", tenant_id=str(tenant_id))
    return trainer


async def create_client_access(
    *,
    tenant_id: UUID,
    client_id: UUID,
    client_name: str,
    email: str,
    password: str,
    auth_repo: AuthRepository,
) -> Trainer:
    """El entrenador da acceso a su cliente para ver el plan (rol 'client')."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise SignupError("Correo inválido")
    if len(password) < 8:
        raise SignupError("La contraseña debe tener al menos 8 caracteres")
    if await auth_repo.get_by_email(email) is not None:
        raise SignupError("Ese correo ya tiene una cuenta")
    account = await auth_repo.create_client_login(
        tenant_id=tenant_id, client_id=client_id, name=client_name,
        email=email, password_hash=hash_password(password),
    )
    logger.info("client_access_created", client_id=str(client_id))
    return account


async def authenticate(*, email: str, password: str, auth_repo: AuthRepository) -> Trainer | None:
    row = await auth_repo.get_by_email(email.strip().lower())
    if row is None:
        return None
    trainer, password_hash = row
    if not verify_password(password, password_hash):
        return None
    return trainer
