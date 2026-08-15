"""Beta cerrada: sin código de invitación no se abre cuenta nueva."""

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
        database_url=f"sqlite+aiosqlite:///{tmp_path}/invite.db",
        anthropic_api_key="",
        beta_invite_code="beta-amigos",
    )
    return Container(settings=settings, tenant_id=DEFAULT_TENANT_ID)


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def test_sin_codigo_de_invitacion_no_crea_cuenta(app) -> None:
    resp = app.post(
        "/registro",
        data={
            "name": "Ana",
            "email": "ana@correo.com",
            "password": "clave-segura-1",
            "invite": "",
        },
    )
    assert resp.status_code == 200
    assert "invitación" in resp.text.lower()
    assert "Código de invitación" in app.get("/registro").text


def test_con_el_codigo_correcto_si_entra(app) -> None:
    resp = app.post(
        "/registro",
        data={
            "name": "Ana",
            "email": "ana@correo.com",
            "password": "clave-segura-1",
            "invite": "beta-amigos",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/consentimiento"
