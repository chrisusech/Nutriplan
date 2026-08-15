"""Registro del token de push que envía el dispositivo nativo."""

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _self: tmp_path / "branding"))
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/push.db",
        anthropic_api_key="",
    )
    return Container(settings=settings, tenant_id=DEFAULT_TENANT_ID)


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _signup(client: TestClient) -> None:
    resp = client.post(
        "/registro",
        data={
            "name": "Ana Pérez",
            "email": "ana@correo.com",
            "password": "clave-segura-1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    client.post("/consentimiento", data={"acepta": "1"}, follow_redirects=False)


def test_la_app_nativa_puede_registrar_su_token_de_push(app) -> None:
    _signup(app)
    resp = app.post(
        "/device-tokens",
        data={"platform": "ios", "token": "apns-token-abc-123"},
    )
    assert resp.status_code == 204


def test_registrar_el_mismo_token_dos_veces_no_falla(app) -> None:
    _signup(app)
    data = {"platform": "android", "token": "fcm-token-xyz"}
    assert app.post("/device-tokens", data=data).status_code == 204
    assert app.post("/device-tokens", data=data).status_code == 204


def test_un_token_con_plataforma_inventada_se_rechaza(app) -> None:
    _signup(app)
    resp = app.post(
        "/device-tokens",
        data={"platform": "windows", "token": "no-vale"},
    )
    assert resp.status_code == 400


def test_sin_sesion_no_se_puede_registrar_push(app) -> None:
    resp = app.post(
        "/device-tokens",
        data={"platform": "ios", "token": "apns"},
        follow_redirects=False,
    )
    assert resp.status_code in (303, 401, 403)
