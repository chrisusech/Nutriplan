"""Validación del alta y acceso, sin HTTP ni SQLAlchemy."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from nutriplan.application.auth import (
    MAX_PASSWORD_LEN,
    SignupError,
    authenticate,
    register,
)
from nutriplan.domain.models import AuthProvider, Role


@pytest.mark.asyncio
async def test_un_correo_sin_arroba_no_crea_cuenta() -> None:
    with pytest.raises(SignupError, match="inválido"):
        await register(
            name="Ana",
            email="sin-arroba",
            password="clave-segura-1",
            auth_repo=AsyncMock(),
            branding_dir=Path("/tmp"),
        )


@pytest.mark.asyncio
async def test_un_correo_gigante_no_crea_cuenta() -> None:
    with pytest.raises(SignupError, match="inválido"):
        await register(
            name="Ana",
            email="a@" + ("x" * 320),
            password="clave-segura-1",
            auth_repo=AsyncMock(),
            branding_dir=Path("/tmp"),
        )


@pytest.mark.asyncio
async def test_una_contrasena_kilometrica_no_crea_cuenta() -> None:
    with pytest.raises(SignupError, match="demasiado larga"):
        await register(
            name="Ana",
            email="ana@correo.com",
            password="x" * (MAX_PASSWORD_LEN + 1),
            auth_repo=AsyncMock(),
            branding_dir=Path("/tmp"),
        )


@pytest.mark.asyncio
async def test_entrar_con_clave_incorrecta_devuelve_none() -> None:
    from nutriplan.adapters.auth import hash_password
    from nutriplan.domain.models import Account

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
    assert await authenticate(
        email="ana@correo.com", password="otra-clave", auth_repo=repo
    ) is None
