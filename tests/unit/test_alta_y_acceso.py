"""Validación del alta y acceso, sin HTTP ni SQLAlchemy."""

from unittest.mock import AsyncMock

import pytest

from nutriplan.application.auth import (
    MAX_PASSWORD_LEN,
    SignupError,
    authenticate,
    register,
)
from nutriplan.domain.models import AuthProvider, Branding, Role


class _BrandingEnMemoria:
    """El alta no toca disco en un test de validación."""

    def save(self, branding: Branding, *, tenant: str) -> None:
        return None


@pytest.mark.asyncio
async def test_un_correo_sin_arroba_no_crea_cuenta() -> None:
    with pytest.raises(SignupError, match="inválido"):
        await register(
            name="Ana",
            email="sin-arroba",
            password="clave-segura-1",
            auth_repo=AsyncMock(),
            branding=_BrandingEnMemoria(),
        )


@pytest.mark.asyncio
async def test_un_correo_gigante_no_crea_cuenta() -> None:
    with pytest.raises(SignupError, match="inválido"):
        await register(
            name="Ana",
            email="a@" + ("x" * 320),
            password="clave-segura-1",
            auth_repo=AsyncMock(),
            branding=_BrandingEnMemoria(),
        )


@pytest.mark.asyncio
async def test_una_contrasena_kilometrica_no_crea_cuenta() -> None:
    with pytest.raises(SignupError, match="demasiado larga"):
        await register(
            name="Ana",
            email="ana@correo.com",
            password="x" * (MAX_PASSWORD_LEN + 1),
            auth_repo=AsyncMock(),
            branding=_BrandingEnMemoria(),
        )


@pytest.mark.asyncio
async def test_entrar_con_clave_incorrecta_devuelve_none() -> None:
    from nutriplan.domain.models import Account
    from nutriplan.domain.passwords import hash_password

    account = Account(
        id=__import__("uuid").uuid4(),
        tenant_id=__import__("uuid").uuid4(),
        name="Ana",
        email="ana@correo.com",
        role=Role.USER,
        provider=AuthProvider.PASSWORD,
    )
    repo = AsyncMock()
    repo.get_by_email.return_value = (account, hash_password("clave-buena-99"))
    assert await authenticate(email="ana@correo.com", password="otra-clave", auth_repo=repo) is None


def test_sin_codigo_de_beta_el_alta_queda_abierta() -> None:
    from nutriplan.application.auth import check_beta_invite

    check_beta_invite(configured="", provided="")
    check_beta_invite(configured="  ", provided="lo-que-sea")


def test_un_codigo_de_beta_incorrecto_bloquea_el_alta() -> None:
    from nutriplan.application.auth import check_beta_invite

    with pytest.raises(SignupError, match="invitación"):
        check_beta_invite(configured="secreto-beta", provided="otro")


def test_el_codigo_de_beta_correcto_deja_pasar() -> None:
    from nutriplan.application.auth import check_beta_invite

    check_beta_invite(configured="secreto-beta", provided="secreto-beta")


@pytest.mark.asyncio
async def test_google_con_el_mismo_correo_se_pega_a_la_cuenta_de_contrasena() -> None:
    from uuid import uuid4

    from nutriplan.application.auth import sign_in_with_provider
    from nutriplan.domain.models import Account

    cuenta = Account(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Ana",
        email="ana@correo.com",
        role=Role.USER,
        provider=AuthProvider.PASSWORD,
    )
    repo = AsyncMock()
    repo.get_by_provider.return_value = None
    repo.get_by_email_any_provider.return_value = cuenta
    vuelta = await sign_in_with_provider(
        provider=AuthProvider.GOOGLE,
        subject="sub-google",
        email="ana@correo.com",
        name="Ana",
        email_verified=True,
        auth_repo=repo,
        branding=_BrandingEnMemoria(),
    )
    assert vuelta.id == cuenta.id
    repo.link_oauth.assert_awaited_once()
    repo.create_account.assert_not_called()


@pytest.mark.asyncio
async def test_entrar_con_google_desactiva_la_contrasena_que_alguien_habia_registrado() -> None:
    """El alta no exige confirmar el correo, así que esa cuenta pudo crearla
    cualquiera con el correo de otro. Quien llega con el proveedor es quien tiene
    el buzón: el que registró antes no puede quedarse dentro."""
    from uuid import uuid4

    from nutriplan.application.auth import sign_in_with_provider
    from nutriplan.domain.models import Account

    cuenta = Account(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Ana",
        email="ana@correo.com",
        role=Role.USER,
        provider=AuthProvider.PASSWORD,
    )
    repo = AsyncMock()
    repo.get_by_provider.return_value = None
    repo.get_by_email_any_provider.return_value = cuenta
    enlaces = AsyncMock()

    await sign_in_with_provider(
        provider=AuthProvider.GOOGLE,
        subject="sub-google",
        email="ana@correo.com",
        name="Ana",
        email_verified=True,
        auth_repo=repo,
        branding=_BrandingEnMemoria(),
        reset_tokens=enlaces,
    )

    repo.clear_password_hash.assert_awaited_once_with(cuenta.id)
    # Y sin enlaces pendientes: si no, recupera por correo y vuelve a entrar.
    enlaces.invalidate_for_user.assert_awaited_once_with(cuenta.id)


@pytest.mark.asyncio
async def test_google_sin_verificar_el_correo_no_se_pega() -> None:
    """Enlazar por correo sin verificar sería un secuestro de cuenta."""
    from uuid import uuid4

    from nutriplan.application.auth import sign_in_with_provider
    from nutriplan.domain.models import Account

    cuenta = Account(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Ana",
        email="ana@correo.com",
        role=Role.USER,
        provider=AuthProvider.PASSWORD,
    )
    repo = AsyncMock()
    repo.get_by_provider.return_value = None
    repo.get_by_email_any_provider.return_value = cuenta
    with pytest.raises(SignupError, match="ya tiene una cuenta"):
        await sign_in_with_provider(
            provider=AuthProvider.GOOGLE,
            subject="sub-google",
            email="ana@correo.com",
            name="Impostor",
            email_verified=False,
            auth_repo=repo,
            branding=_BrandingEnMemoria(),
        )
    repo.link_oauth.assert_not_called()


@pytest.mark.asyncio
async def test_apple_no_pisa_una_cuenta_que_nacio_en_google() -> None:
    from uuid import uuid4

    from nutriplan.application.auth import sign_in_with_provider
    from nutriplan.domain.models import Account

    cuenta = Account(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Ana",
        email="ana@correo.com",
        role=Role.USER,
        provider=AuthProvider.GOOGLE,
    )
    repo = AsyncMock()
    repo.get_by_provider.return_value = None
    repo.get_by_email_any_provider.return_value = cuenta
    with pytest.raises(SignupError, match="ya tiene una cuenta"):
        await sign_in_with_provider(
            provider=AuthProvider.APPLE,
            subject="sub-apple",
            email="ana@correo.com",
            name="Ana",
            email_verified=True,
            auth_repo=repo,
            branding=_BrandingEnMemoria(),
        )


@pytest.mark.asyncio
async def test_apple_sin_correo_entra_si_el_sub_ya_existe() -> None:
    from uuid import uuid4

    from nutriplan.application.auth import sign_in_with_provider
    from nutriplan.domain.models import Account

    cuenta = Account(
        id=uuid4(),
        tenant_id=uuid4(),
        name="Ana",
        email="ana@correo.com",
        role=Role.USER,
        provider=AuthProvider.APPLE,
    )
    repo = AsyncMock()
    repo.get_by_provider.return_value = cuenta
    vuelta = await sign_in_with_provider(
        provider=AuthProvider.APPLE,
        subject="sub-apple",
        email="",
        name="",
        email_verified=False,
        auth_repo=repo,
        branding=_BrandingEnMemoria(),
    )
    assert vuelta.id == cuenta.id
    repo.create_account.assert_not_called()


@pytest.mark.asyncio
async def test_apple_sin_correo_no_crea_una_cuenta_nueva() -> None:
    from nutriplan.application.auth import sign_in_with_provider

    repo = AsyncMock()
    repo.get_by_provider.return_value = None
    with pytest.raises(SignupError, match="no envió un correo"):
        await sign_in_with_provider(
            provider=AuthProvider.APPLE,
            subject="sub-nuevo",
            email="",
            name="",
            email_verified=False,
            auth_repo=repo,
            branding=_BrandingEnMemoria(),
        )
    repo.create_account.assert_not_called()
