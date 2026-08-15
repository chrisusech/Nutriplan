"""Que nadie siga viendo el diseño de ayer.

Las hojas que `app.css` importa y la fuente de iconos no pueden llevar `?v=` en
la URL, así que la única forma de que el navegador no se quede con la copia
vieja es la cabecera. Cuando faltó, la app se veía con las tarjetas sin margen y
los iconos como palabras sueltas.
"""

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
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/estaticos.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )
    with TestClient(create_app(container)) as client:
        yield client


def test_una_hoja_importada_se_revalida_en_cada_visita(app) -> None:
    resp = app.get("/static/styles/components.css")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_la_fuente_de_iconos_tambien_se_revalida_si_va_sin_version(app) -> None:
    resp = app.get("/static/fonts/material-symbols-rounded.woff2")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_lo_que_lleva_version_en_la_url_se_guarda_para_siempre(app) -> None:
    resp = app.get("/static/app.css", params={"v": "123"})
    assert resp.status_code == 200
    assert "immutable" in resp.headers["cache-control"]


def test_la_pestana_del_navegador_muestra_el_logo_y_no_un_mundo(app) -> None:
    """Sin `rel="icon"` el navegador pinta su globo genérico, y la app parece
    una página cualquiera entre las diez pestañas abiertas."""
    assert '<link rel="icon" href="/static/icon-32.png"' in app.get("/login").text
    assert app.get("/static/icon-32.png").status_code == 200


def test_el_navegador_que_pide_favicon_ico_a_secas_encuentra_el_logo(app) -> None:
    resp = app.get("/favicon.ico", follow_redirects=False)
    assert resp.status_code == 301
    assert resp.headers["location"] == "/static/icon-32.png"
