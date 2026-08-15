"""Recuperación de contraseña: el enlace se usa una vez y se muere.

El enlace viaja por correo, que es el sitio menos privado que hay: reenviado,
en un buzón compartido, en una captura a soporte. Así que estas historias son
sobre lo que pasa DESPUÉS de usarlo.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from nutriplan.application.auth import SignupError
from nutriplan.application.password_reset import (
    complete_password_reset,
    email_behind_reset_link,
    request_password_reset,
)
from nutriplan.domain.models import Account, Role
from nutriplan.domain.password_reset import PasswordResetToken, token_fingerprint

AHORA = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
TENANT = uuid4()


def _cuenta() -> Account:
    return Account(
        id=uuid4(),
        tenant_id=TENANT,
        email="ana@correo.com",
        name="Ana",
        password_hash="x",
        role=Role.USER,
    )


class _Enlaces:
    """Los enlaces vivos, en memoria. Guarda el hash, nunca el token."""

    def __init__(self) -> None:
        self.filas: list[PasswordResetToken] = []
        self.usados: set[str] = set()

    async def add(self, token: PasswordResetToken) -> None:
        self.filas.append(token)

    def _vivo(self, token_hash: str, now: datetime) -> PasswordResetToken | None:
        if token_hash in self.usados:
            return None
        return next(
            (f for f in self.filas if f.token_hash == token_hash and f.expires_at > now),
            None,
        )

    async def peek(self, token_hash: str, *, now: datetime) -> UUID | None:
        fila = self._vivo(token_hash, now)
        return fila.user_id if fila else None

    async def consume(self, token_hash: str, *, now: datetime) -> UUID | None:
        fila = self._vivo(token_hash, now)
        if fila is None:
            return None
        self.usados.add(token_hash)
        return fila.user_id

    async def invalidate_for_user(self, user_id: UUID) -> None:
        self.filas = [f for f in self.filas if f.user_id != user_id]


def _repo_con(cuenta: Account) -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_email.return_value = (cuenta, "hash-viejo")
    repo.get_by_id.return_value = cuenta
    repo.set_password_hash.return_value = True
    return repo


async def _pedir(cuenta: Account, enlaces: _Enlaces, repo: AsyncMock) -> str:
    link = await request_password_reset(
        email=cuenta.email, auth_repo=repo, tokens=enlaces, now=AHORA
    )
    assert link is not None
    return link.url_path.removeprefix("/recuperar/")


async def test_un_correo_invalido_no_genera_enlace() -> None:
    with pytest.raises(SignupError, match="inválido"):
        await request_password_reset(email="hola", auth_repo=AsyncMock(), tokens=_Enlaces())


async def test_un_correo_sin_cuenta_no_dice_que_no_existe() -> None:
    """La respuesta es la misma exista o no: si no, es un detector de correos."""
    repo = AsyncMock()
    repo.get_by_email.return_value = None
    assert (
        await request_password_reset(email="nadie@correo.com", auth_repo=repo, tokens=_Enlaces())
        is None
    )


async def test_del_enlace_solo_se_guarda_su_huella() -> None:
    """Un volcado de la tabla no puede entregar enlaces usables."""
    cuenta, enlaces = _cuenta(), _Enlaces()
    token = await _pedir(cuenta, enlaces, _repo_con(cuenta))

    assert enlaces.filas[0].token_hash == token_fingerprint(token)
    assert token not in enlaces.filas[0].token_hash


async def test_un_enlace_de_recuperacion_no_sirve_dos_veces() -> None:
    """Reenviado o en el historial, el enlace ya gastado no vuelve a abrir nada."""
    cuenta, enlaces = _cuenta(), _Enlaces()
    repo = _repo_con(cuenta)
    token = await _pedir(cuenta, enlaces, repo)

    assert (
        await complete_password_reset(
            token=token, new_password="nueva-clave-99", auth_repo=repo, tokens=enlaces, now=AHORA
        )
        == cuenta.email
    )

    with pytest.raises(SignupError, match="expiró"):
        await complete_password_reset(
            token=token, new_password="otra-clave-77", auth_repo=repo, tokens=enlaces, now=AHORA
        )


async def test_cambiar_la_contrasena_invalida_los_enlaces_pendientes() -> None:
    """Quien recupera su cuenta cierra todas las puertas, no solo la que usó."""
    cuenta, enlaces = _cuenta(), _Enlaces()
    repo = _repo_con(cuenta)
    usado = await _pedir(cuenta, enlaces, repo)

    await complete_password_reset(
        token=usado, new_password="nueva-clave-99", auth_repo=repo, tokens=enlaces, now=AHORA
    )
    assert enlaces.filas == []


async def test_pedir_un_enlace_nuevo_retira_el_anterior() -> None:
    """Dos llaves vivas y solo se acuerda de una: la vieja sobra."""
    cuenta, enlaces = _cuenta(), _Enlaces()
    repo = _repo_con(cuenta)
    viejo = await _pedir(cuenta, enlaces, repo)
    await _pedir(cuenta, enlaces, repo)

    with pytest.raises(SignupError, match="expiró"):
        await complete_password_reset(
            token=viejo, new_password="nueva-clave-99", auth_repo=repo, tokens=enlaces, now=AHORA
        )


async def test_un_enlace_caducado_pide_uno_nuevo() -> None:
    cuenta, enlaces = _cuenta(), _Enlaces()
    repo = _repo_con(cuenta)
    token = await _pedir(cuenta, enlaces, repo)

    with pytest.raises(SignupError, match="expiró"):
        await complete_password_reset(
            token=token,
            new_password="nueva-clave-99",
            auth_repo=repo,
            tokens=enlaces,
            now=AHORA + timedelta(hours=2),
        )


async def test_un_token_inventado_no_cambia_la_clave() -> None:
    with pytest.raises(SignupError, match="expiró"):
        await complete_password_reset(
            token="me-lo-invento",
            new_password="nueva-clave-99",
            auth_repo=AsyncMock(),
            tokens=_Enlaces(),
            now=AHORA,
        )


async def test_si_la_cuenta_ya_no_esta_el_token_no_sirve() -> None:
    cuenta, enlaces = _cuenta(), _Enlaces()
    repo = _repo_con(cuenta)
    token = await _pedir(cuenta, enlaces, repo)
    repo.get_by_id.return_value = None

    with pytest.raises(SignupError, match="expiró"):
        await complete_password_reset(
            token=token, new_password="nueva-clave-99", auth_repo=repo, tokens=enlaces, now=AHORA
        )


async def test_si_el_repo_no_guarda_el_hash_se_avisa() -> None:
    cuenta, enlaces = _cuenta(), _Enlaces()
    repo = _repo_con(cuenta)
    token = await _pedir(cuenta, enlaces, repo)
    repo.set_password_hash.return_value = False

    with pytest.raises(SignupError, match="No se pudo actualizar"):
        await complete_password_reset(
            token=token, new_password="nueva-clave-99", auth_repo=repo, tokens=enlaces, now=AHORA
        )


async def test_la_pantalla_muestra_de_quien_es_el_enlace_sin_gastarlo() -> None:
    cuenta, enlaces = _cuenta(), _Enlaces()
    repo = _repo_con(cuenta)
    token = await _pedir(cuenta, enlaces, repo)

    assert (
        await email_behind_reset_link(token=token, auth_repo=repo, tokens=enlaces, now=AHORA)
        == cuenta.email
    )
    # Y sigue sirviendo después de mirarlo.
    assert (
        await complete_password_reset(
            token=token, new_password="nueva-clave-99", auth_repo=repo, tokens=enlaces, now=AHORA
        )
        == cuenta.email
    )
