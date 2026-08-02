"""Las garantías de seguridad, una por una.

No son detalles de implementación: son promesas que la app le hace a quien se
registra. Si algo de aquí se pone en rojo, la app no se despliega.
"""

import re

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/s.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _registrar(client: TestClient, email: str = "ana@correo.com") -> None:
    resp = client.post(
        "/registro",
        data={"name": "Ana", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


# --- CSRF -------------------------------------------------------------------


def test_un_post_sin_token_csrf_se_rechaza(app) -> None:
    """`SameSite=Lax` es una sola línea de defensa y depende del navegador."""
    resp = app.post(
        "/registro",
        data={"name": "X", "email": "x@y.co", "password": "clave-larga-1"},
        headers={"X-CSRF-Token": ""},
    )
    assert resp.status_code == 403


def test_un_post_con_un_token_ajeno_se_rechaza(app) -> None:
    resp = app.post(
        "/registro",
        data={"name": "X", "email": "x@y.co", "password": "clave-larga-1"},
        headers={"X-CSRF-Token": "token-inventado-de-otro-sitio"},
    )
    assert resp.status_code == 403


def test_el_formulario_trae_el_token_para_poder_enviarlo(app) -> None:
    assert re.search(r'name="_csrf" value="[^"]+"', app.get("/login").text)


def test_el_login_social_no_exige_csrf(app) -> None:
    """El ID token llega del plugin nativo, que no tiene cookie ni token."""
    resp = app.post(
        "/auth/oauth/google", data={"id_token": "x"},
        headers={"X-CSRF-Token": ""}, follow_redirects=False,
    )
    assert resp.status_code != 403


# --- Cabeceras --------------------------------------------------------------


def test_toda_respuesta_lleva_las_cabeceras_de_seguridad(app) -> None:
    h = app.get("/login").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "strict-origin" in h["Referrer-Policy"]
    assert "geolocation=()" in h["Permissions-Policy"]


def test_la_csp_no_permite_javascript_ni_estilos_incrustados(app) -> None:
    """Es lo que obliga a que el JS viva en app.js y el color de marca en un
    atributo. Relajarlo aquí desharía ese trabajo."""
    csp = app.get("/login").headers["Content-Security-Policy"]
    assert "unsafe-inline" not in csp
    assert "unsafe-eval" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


def test_ninguna_pagina_trae_estilos_ni_scripts_incrustados(app) -> None:
    """Si vuelve un <style> inline, la CSP lo bloquea y la app se ve rota."""
    html = app.get("/login").text
    assert "<style>" not in html
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)


# --- Límite de intentos -----------------------------------------------------


def test_adivinar_contrasenas_se_frena(app) -> None:
    """Sin tope, probar claves contra un scrypt de 16 MB agota la CPU."""
    _registrar(app)
    app.post("/logout", follow_redirects=False)

    codes = [
        app.post("/login", data={"email": "ana@correo.com", "password": f"mala{i}"}).status_code
        for i in range(14)
    ]
    assert 429 in codes, "el login debería cortarse tras unos intentos"


# --- Sesión -----------------------------------------------------------------


def test_la_sesion_se_renueva_al_entrar(app) -> None:
    """Si alguien fija una sesión previa, entrar no puede heredarla."""
    antes = app.get("/login")
    token_antes = re.search(r'name="_csrf" value="([^"]+)"', antes.text).group(1)
    _registrar(app)
    token_despues = re.search(r'name="_csrf" value="([^"]+)"', app.get("/login").text).group(1)
    assert token_antes != token_despues


def test_sin_sesion_toda_ruta_privada_lleva_al_login(app) -> None:
    for ruta in ("/", "/perfil", "/feedback", "/onboarding"):
        resp = app.get(ruta, follow_redirects=False)
        assert resp.status_code == 303, ruta
        assert resp.headers["location"] == "/login"


# --- Errores ----------------------------------------------------------------


def test_un_identificador_corrupto_da_400_y_no_una_traza(app) -> None:
    """Antes `UUID(raw)` reventaba en un 500 con traza, que además le contaba
    al curioso más de lo debido sobre el servidor."""
    _registrar(app)
    resp = app.get("/planes/esto-no-es-un-uuid", follow_redirects=False)
    assert resp.status_code == 400
    assert "Traceback" not in resp.text


def test_produccion_no_publica_el_mapa_de_rutas(tmp_path, monkeypatch) -> None:
    """`/docs` abierto le regala el mapa de la app a cualquier escáner."""
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    prod = create_app(
        Container(
            settings=Settings(
                env="prod",
                session_secret="x" * 48,
                database_url=f"sqlite+aiosqlite:///{tmp_path}/p.db",
            ),
            tenant_id=DEFAULT_TENANT_ID,
        )
    )
    # Sin arrancar la app: lo que importa es que las rutas no existan.
    assert prod.docs_url is None
    assert prod.openapi_url is None
    assert prod.redoc_url is None


def test_un_formulario_normal_funciona_con_el_campo_oculto(app) -> None:
    """El hueco que tenían los otros tests: mandan el token por cabecera y así
    el middleware nunca toca el cuerpo. Un `<form method="post">` de verdad no
    manda cabeceras, y leer el formulario para sacar el token dejaba al
    endpoint sin cuerpo que parsear — todo POST respondía 422.
    """
    token = re.search(r'name="_csrf" value="([^"]+)"', app.get("/login").text).group(1)
    resp = app.post(
        "/registro",
        data={
            "name": "Ana", "email": "form@correo.com",
            "password": "clave-segura-1", "_csrf": token,
        },
        headers={"X-CSRF-Token": ""},   # el helper de tests no interviene
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def test_las_pantallas_del_usuario_no_usan_estilos_inline(app) -> None:
    """La CSP bloquea el atributo `style=`, así que una plantilla que lo use se
    ve rota: sin ancho, sin padding, desbordada. Pasó con el login.
    """
    from pathlib import Path

    templates = Path(__file__).resolve().parents[2] / "src/nutriplan/ui/web/templates"
    publicas = [
        "base.html", "auth.html", "week.html", "onboarding.html",
        "profile.html", "feedback.html",
        "password_reset_request.html", "password_reset_confirm.html",
    ]
    con_estilos = [
        n for n in publicas if 'style="' in (templates / n).read_text(encoding="utf-8")
    ]
    assert not con_estilos, f"la CSP romperá estas pantallas: {con_estilos}"
