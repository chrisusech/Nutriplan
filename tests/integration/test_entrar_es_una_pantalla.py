"""Entrar es una pantalla, no una hoja modal.

En el iPhone un <dialog> + teclado cuelga el WebView. Estas historias
clavan que Comenzar e Inicia sesión navegan de verdad, y que una clave
mala se queda en el formulario.
"""

from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app


def _app(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    container = Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/e.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )
    return TestClient(create_app(container))


def test_la_bienvenida_lleva_a_entrar_sin_abrir_una_hoja(tmp_path, monkeypatch) -> None:
    with _app(tmp_path, monkeypatch) as client:
        html = client.get("/login").text
        assert "Comenzar" in html
        assert 'href="/entrar"' in html
        assert "sheet-login" not in html
        assert "data-sheet-open" not in html


def test_entrar_es_un_formulario_de_pagina(tmp_path, monkeypatch) -> None:
    with _app(tmp_path, monkeypatch) as client:
        resp = client.get("/entrar")
        assert resp.status_code == 200
        html = resp.text
        assert "Hola de nuevo" in html
        assert 'action="/login"' in html
        assert "sheet-login" not in html
        assert "page-form" in html


def test_una_clave_incorrecta_se_queda_en_el_formulario(tmp_path, monkeypatch) -> None:
    with _app(tmp_path, monkeypatch) as client:
        alta = client.post(
            "/registro",
            data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
            follow_redirects=False,
        )
        assert alta.status_code == 303
        client.post("/logout", follow_redirects=False)
        resp = client.post(
            "/login",
            data={"email": "ana@correo.com", "password": "clave-que-no-es"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "Correo o contraseña incorrectos." in resp.text
        assert "sheet-login" not in resp.text
        assert "Hola de nuevo" in resp.text
        assert "page-form" in resp.text


def test_quien_ya_entro_no_vuelve_a_la_bienvenida_con_atras(tmp_path, monkeypatch) -> None:
    """Atrás en el iPhone no puede parecer un cierre de sesión."""
    with _app(tmp_path, monkeypatch) as client:
        alta = client.post(
            "/registro",
            data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
            follow_redirects=False,
        )
        assert alta.status_code == 303
        for ruta in ("/login", "/entrar"):
            resp = client.get(ruta, follow_redirects=False)
            assert resp.status_code == 303, ruta
            assert resp.headers["location"] == "/"
