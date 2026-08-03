"""Recuperación de contraseña: bordes que el HTTP no suele tocar."""

from unittest.mock import AsyncMock

import pytest

from nutriplan.adapters.password_reset_token import issue_token
from nutriplan.application.auth import SignupError
from nutriplan.application.password_reset import (
    complete_password_reset,
    request_password_reset,
)

SECRET = "test-session-secret"


@pytest.mark.asyncio
async def test_un_correo_invalido_no_genera_enlace() -> None:
    with pytest.raises(SignupError, match="inválido"):
        await request_password_reset(
            email="hola",
            auth_repo=AsyncMock(),
            session_secret=SECRET,
        )


@pytest.mark.asyncio
async def test_un_token_caducado_no_cambia_la_clave() -> None:
    token = issue_token(email="ana@correo.com", secret=SECRET, ttl_seconds=-1)
    with pytest.raises(SignupError, match="expiró"):
        await complete_password_reset(
            token=token,
            new_password="nueva-clave-99",
            auth_repo=AsyncMock(),
            session_secret=SECRET,
        )


@pytest.mark.asyncio
async def test_si_la_cuenta_ya_no_esta_el_token_no_sirve() -> None:
    token = issue_token(email="ana@correo.com", secret=SECRET, ttl_seconds=3600)
    repo = AsyncMock()
    repo.get_by_email.return_value = None
    with pytest.raises(SignupError, match="expiró"):
        await complete_password_reset(
            token=token,
            new_password="nueva-clave-99",
            auth_repo=repo,
            session_secret=SECRET,
        )


@pytest.mark.asyncio
async def test_si_el_repo_no_guarda_el_hash_se_avisa() -> None:
    token = issue_token(email="ana@correo.com", secret=SECRET, ttl_seconds=3600)
    repo = AsyncMock()
    repo.get_by_email.return_value = object()
    repo.set_password_hash.return_value = False
    with pytest.raises(SignupError, match="No se pudo actualizar"):
        await complete_password_reset(
            token=token,
            new_password="nueva-clave-99",
            auth_repo=repo,
            session_secret=SECRET,
        )
