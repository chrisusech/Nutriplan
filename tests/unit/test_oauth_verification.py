"""Verificación de los ID token de Google, sin red.

Es la frontera de la app: lo que aquí pase por bueno abre una sesión. Los casos
que importan son los que un atacante intentaría, no el feliz.
"""

import httpx
import pytest

from nutriplan.adapters.oauth import OAuthError, verify_google_id_token
from nutriplan.domain.models import AuthProvider

CLIENT_ID = "mi-app.apps.googleusercontent.com"


def _google_responds(payload: dict, status: int = 200, monkeypatch=None) -> None:
    """Sustituye la llamada a Google por una respuesta grabada."""

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            return httpx.Response(status, json=payload)

    monkeypatch.setattr(
        "nutriplan.adapters.oauth.httpx.AsyncClient", lambda **kw: _FakeClient()
    )


async def test_un_token_bueno_devuelve_la_identidad_de_quien_entra(monkeypatch) -> None:
    _google_responds(
        {
            "aud": CLIENT_ID,
            "sub": "1234567890",
            "email": "ana@gmail.com",
            "email_verified": "true",
            "name": "Ana Pérez",
        },
        monkeypatch=monkeypatch,
    )
    identity = await verify_google_id_token("t", client_id=CLIENT_ID)
    assert identity.provider is AuthProvider.GOOGLE
    assert identity.subject == "1234567890"
    assert identity.email == "ana@gmail.com"
    assert identity.email_verified is True


async def test_un_token_emitido_para_otra_app_no_sirve_aqui(monkeypatch) -> None:
    """Sin comprobar `aud`, el token de cualquier otra app abriría sesión."""
    _google_responds(
        {"aud": "otra-app.apps.googleusercontent.com", "sub": "1", "email": "a@b.co"},
        monkeypatch=monkeypatch,
    )
    with pytest.raises(OAuthError, match="no es para esta aplicación"):
        await verify_google_id_token("t", client_id=CLIENT_ID)


async def test_un_token_que_google_rechaza_no_pasa(monkeypatch) -> None:
    _google_responds({"error": "invalid_token"}, status=400, monkeypatch=monkeypatch)
    with pytest.raises(OAuthError, match="no es válido"):
        await verify_google_id_token("caducado", client_id=CLIENT_ID)


async def test_un_token_sin_identidad_utilizable_se_rechaza(monkeypatch) -> None:
    _google_responds({"aud": CLIENT_ID, "sub": "", "email": ""}, monkeypatch=monkeypatch)
    with pytest.raises(OAuthError, match="identidad utilizable"):
        await verify_google_id_token("t", client_id=CLIENT_ID)


async def test_un_correo_sin_verificar_por_google_llega_marcado_como_tal(monkeypatch) -> None:
    _google_responds(
        {"aud": CLIENT_ID, "sub": "1", "email": "a@b.co", "email_verified": "false"},
        monkeypatch=monkeypatch,
    )
    identity = await verify_google_id_token("t", client_id=CLIENT_ID)
    assert identity.email_verified is False


async def test_sin_client_id_configurado_no_se_verifica_nada() -> None:
    """Mejor fallar que aceptar todo porque falta una variable de entorno."""
    with pytest.raises(OAuthError, match="GOOGLE_CLIENT_ID"):
        await verify_google_id_token("t", client_id="")


async def test_si_google_no_responde_no_se_deja_entrar_por_las_dudas(monkeypatch) -> None:
    class _BrokenClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None):
            raise httpx.ConnectError("sin red")

    monkeypatch.setattr(
        "nutriplan.adapters.oauth.httpx.AsyncClient", lambda **kw: _BrokenClient()
    )
    with pytest.raises(OAuthError, match="No se pudo verificar"):
        await verify_google_id_token("t", client_id=CLIENT_ID)
