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


# --- Apple -----------------------------------------------------------------


APPLE_CLIENT = "com.nutriplan.app"


async def test_sin_apple_client_id_no_se_verifica_nada() -> None:
    from nutriplan.adapters.oauth import verify_apple_id_token

    with pytest.raises(OAuthError, match="APPLE_CLIENT_ID"):
        await verify_apple_id_token("t", client_id="")


async def test_un_token_de_apple_invalido_no_pasa(monkeypatch) -> None:
    from nutriplan.adapters.oauth import verify_apple_id_token

    class _BrokenKeys:
        def get_signing_key_from_jwt(self, _token: str) -> object:
            raise ValueError("firma rota")

    monkeypatch.setattr(
        "jwt.PyJWKClient", lambda *_a, **_k: _BrokenKeys()
    )
    with pytest.raises(OAuthError, match="no es válido"):
        await verify_apple_id_token("basura", client_id=APPLE_CLIENT)


async def test_un_token_de_apple_sin_subject_se_rechaza(monkeypatch) -> None:
    from nutriplan.adapters.oauth import verify_apple_id_token

    class _Key:
        key = "k"

    class _Keys:
        def get_signing_key_from_jwt(self, _token: str) -> _Key:
            return _Key()

    monkeypatch.setattr("jwt.PyJWKClient", lambda *_a, **_k: _Keys())
    monkeypatch.setattr(
        "jwt.decode",
        lambda *_a, **_k: {"email": "ana@icloud.com", "email_verified": True},
    )
    with pytest.raises(OAuthError, match="identidad utilizable"):
        await verify_apple_id_token("t", client_id=APPLE_CLIENT)


async def test_un_token_bueno_de_apple_devuelve_identidad(monkeypatch) -> None:
    from nutriplan.adapters.oauth import verify_apple_id_token

    class _Key:
        key = "k"

    class _Keys:
        def get_signing_key_from_jwt(self, _token: str) -> _Key:
            return _Key()

    monkeypatch.setattr("jwt.PyJWKClient", lambda *_a, **_k: _Keys())
    monkeypatch.setattr(
        "jwt.decode",
        lambda *_a, **_k: {
            "sub": "apple.sub.1",
            "email": "ana@icloud.com",
            "email_verified": True,
        },
    )
    identity = await verify_apple_id_token("t", client_id=APPLE_CLIENT)
    assert identity.provider is AuthProvider.APPLE
    assert identity.subject == "apple.sub.1"
    assert identity.email == "ana@icloud.com"
    assert identity.name == ""
    assert identity.email_verified is True
