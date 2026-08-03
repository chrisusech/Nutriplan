"""Rutas públicas que las tiendas exigen poder abrir sin cuenta."""

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    container = Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/legal.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )
    with TestClient(create_app(container)) as client:
        yield client


def test_health_responde_ok_sin_sesion(app) -> None:
    resp = app.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_terminos_y_soporte_y_privacidad_son_publicos(app) -> None:
    for path in ("/terminos", "/soporte", "/privacidad"):
        resp = app.get(path)
        assert resp.status_code == 200, path
        assert "NutriPlan" in resp.text or "nutri" in resp.text.lower()
