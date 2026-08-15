"""Alta y acceso de cuentas.

Cada persona es dueña de su propio tenant; el aislamiento lo garantizan los repos.
El alta es pública: cualquiera se registra con correo, con Google o con Apple. El
`super_user` sigue siendo una cuenta más, solo que con rol distinto.
"""

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

import structlog

from nutriplan.domain.models import Account, AuthProvider, Branding, Role
from nutriplan.domain.passwords import hash_password, verify_password
from nutriplan.ports.branding import BrandingStore

logger = structlog.get_logger(__name__)

MIN_PASSWORD_LEN = 8
# scrypt cuesta ~16 MB por intento: sin tope, una contraseña larguísima es un
# vector de agotamiento de memoria disfrazado de registro.
MAX_PASSWORD_LEN = 200


class AuthRepository(Protocol):
    async def get_by_email(self, email: str) -> tuple[Account, str] | None: ...
    async def get_by_email_any_provider(self, email: str) -> Account | None: ...
    async def get_by_provider(self, provider: AuthProvider, subject: str) -> Account | None: ...
    async def create_account(
        self,
        *,
        tenant_id: UUID,
        tenant_name: str,
        name: str,
        email: str,
        password_hash: str | None = None,
        role: str = "user",
        provider: AuthProvider = AuthProvider.PASSWORD,
        provider_subject: str | None = None,
        email_verified_at: datetime | None = None,
    ) -> Account: ...
    async def get_by_id(self, user_id: UUID) -> Account | None: ...
    async def set_password_hash(self, email: str, password_hash: str) -> bool: ...
    async def clear_password_hash(self, user_id: UUID) -> None: ...
    async def set_name(self, user_id: UUID, name: str) -> None: ...
    async def mark_email_verified(self, user_id: UUID) -> None: ...
    async def touch_login(self, user_id: UUID) -> None: ...
    async def link_oauth(self, user_id: UUID, provider: AuthProvider, subject: str) -> None: ...


class PasswordCredentialSink(Protocol):
    """Lo único que el alta social necesita saber de los enlaces pendientes."""

    async def invalidate_for_user(self, user_id: UUID) -> None: ...


class SignupError(ValueError):
    """El alta no se pudo completar (correo duplicado, datos inválidos)."""


def check_beta_invite(*, configured: str, provided: str) -> None:
    """Si hay código de beta, el alta pública lo exige; vacío = abierto."""
    expected = configured.strip()
    if not expected:
        return
    if provided.strip() != expected:
        raise SignupError("Necesitas un código de invitación válido para esta beta.")


def _validate_email(email: str) -> str:
    email = email.strip().lower()
    if not email or "@" not in email or len(email) > 320:
        raise SignupError("Correo inválido")
    return email


def _validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise SignupError(f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres")
    if len(password) > MAX_PASSWORD_LEN:
        raise SignupError("La contraseña es demasiado larga")


async def _new_account(
    *,
    name: str,
    email: str,
    auth_repo: AuthRepository,
    branding: BrandingStore,
    role: Role,
    provider: AuthProvider,
    password_hash: str | None = None,
    provider_subject: str | None = None,
    email_verified_at: datetime | None = None,
) -> Account:
    display = name.strip() or email.split("@")[0]
    tenant_id = uuid4()
    account = await auth_repo.create_account(
        tenant_id=tenant_id,
        tenant_name=display,
        name=display,
        email=email,
        password_hash=password_hash,
        role=role.value,
        provider=provider,
        provider_subject=provider_subject,
        email_verified_at=email_verified_at,
    )
    branding.save(Branding(tenant_name=display), tenant=str(tenant_id))
    logger.info(
        "account_created",
        tenant_id=str(tenant_id),
        role=role.value,
        provider=provider.value,
    )
    return account


async def register(
    *,
    name: str,
    email: str,
    password: str,
    auth_repo: AuthRepository,
    branding: BrandingStore,
    role: Role = Role.USER,
) -> Account:
    """Alta pública con correo y contraseña."""
    email = _validate_email(email)
    _validate_password(password)
    if await auth_repo.get_by_email_any_provider(email) is not None:
        raise SignupError("Ese correo ya tiene una cuenta")
    return await _new_account(
        name=name,
        email=email,
        auth_repo=auth_repo,
        branding=branding,
        role=role,
        provider=AuthProvider.PASSWORD,
        password_hash=hash_password(password),
    )


async def sign_in_with_provider(
    *,
    provider: AuthProvider,
    subject: str,
    email: str,
    name: str,
    email_verified: bool,
    auth_repo: AuthRepository,
    branding: BrandingStore,
    reset_tokens: PasswordCredentialSink | None = None,
) -> Account:
    """Entra con Google o Apple; si es la primera vez, crea la cuenta.

    `subject` es el identificador estable del proveedor, y es la clave real: el
    correo de Apple puede ser un relay que cambia, y el de Google puede cambiar de
    dueño. Enlazar por correo sin verificar sería un secuestro de cuenta.
    """
    existing = await auth_repo.get_by_provider(provider, subject)
    if existing is not None:
        await auth_repo.touch_login(existing.id)
        return existing

    email = _validate_email(email)
    clash = await auth_repo.get_by_email_any_provider(email)
    if clash is not None:
        # Mismo correo, cuenta de contraseña, token verificado: no duplicar.
        # Google contra Apple en la misma fila no cabe sin tabla de identidades.
        if clash.provider is AuthProvider.PASSWORD and email_verified:
            # La contraseña que hubiera se anula, y con ella los enlaces de
            # recuperación pendientes. El alta no exige confirmar el correo, así
            # que esa cuenta pudo crearla cualquiera con el correo de otro: quien
            # llega ahora con el proveedor es quien de verdad tiene el buzón, y
            # el otro no puede quedarse dentro.
            await auth_repo.clear_password_hash(clash.id)
            if reset_tokens is not None:
                await reset_tokens.invalidate_for_user(clash.id)
            await auth_repo.link_oauth(clash.id, provider, subject)
            await auth_repo.mark_email_verified(clash.id)
            await auth_repo.touch_login(clash.id)
            logger.info("oauth_linked_over_password", user_id=str(clash.id))
            return clash
        raise SignupError(
            "Ese correo ya tiene una cuenta creada de otra forma. "
            "Entra como lo hiciste la primera vez."
        )
    return await _new_account(
        name=name,
        email=email,
        auth_repo=auth_repo,
        branding=branding,
        role=Role.USER,
        provider=provider,
        provider_subject=subject,
        email_verified_at=datetime.now(UTC) if email_verified else None,
    )


async def authenticate(*, email: str, password: str, auth_repo: AuthRepository) -> Account | None:
    row = await auth_repo.get_by_email(email.strip().lower())
    if row is None:
        return None
    account, password_hash = row
    if not verify_password(password, password_hash):
        return None
    await auth_repo.touch_login(account.id)
    return account
