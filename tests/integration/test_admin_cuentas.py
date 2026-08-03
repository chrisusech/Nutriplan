"""La consola del super_user: ver las cuentas del BETA y poder intervenir.

Es la única parte de la app que no es de usuario final, y la única que cruza
tenants. Por eso lo primero que se prueba es quién NO puede entrar.
"""

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app

ADMIN = "admin@nutriplan.test"
CLAVE_ADMIN = "clave-admin-1"


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/admin.db",
            admin_email=ADMIN,
            admin_password=CLAVE_ADMIN,
        ),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _como_admin(app: TestClient) -> None:
    app.post("/logout", follow_redirects=False)
    resp = app.post(
        "/login", data={"email": ADMIN, "password": CLAVE_ADMIN}, follow_redirects=False
    )
    assert resp.status_code == 303, resp.text


def _como_usuaria(app: TestClient, email: str = "ana@correo.com") -> None:
    app.post("/logout", follow_redirects=False)
    app.post(
        "/registro",
        data={"name": "Ana", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    )


def _id_de(app: TestClient, email: str) -> str:
    import re

    html = app.get("/admin/cuentas").text
    bloque = html[html.index(email):]
    encontrado = re.search(r"/admin/cuentas/([0-9a-f-]{36})/", bloque)
    assert encontrado is not None, bloque[:400]
    return encontrado.group(1)


# --- Quién entra ------------------------------------------------------------


def test_una_usuaria_normal_no_llega_a_la_consola(app) -> None:
    _como_usuaria(app)
    resp = app.get("/admin/cuentas", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_una_usuaria_normal_tampoco_puede_bloquear_a_nadie(app) -> None:
    """Esconder la pantalla no basta si la ruta sigue abierta."""
    _como_admin(app)
    _como_usuaria(app, "otra@correo.com")
    _como_admin(app)
    victima = _id_de(app, "otra@correo.com")

    _como_usuaria(app, "atacante@correo.com")
    resp = app.post(f"/admin/cuentas/{victima}/bloquear", follow_redirects=False)
    assert resp.headers["location"] == "/"

    _como_admin(app)
    assert "Bloqueada" not in app.get("/admin/cuentas").text


def test_sin_sesion_no_se_ve_nada(app) -> None:
    app.post("/logout", follow_redirects=False)
    resp = app.get("/admin/cuentas", follow_redirects=False)
    assert resp.status_code == 303


# --- Lo que el super_user puede hacer ---------------------------------------


def test_el_super_user_ve_las_cuentas_que_se_han_registrado(app) -> None:
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    assert "ana@correo.com" in app.get("/admin/cuentas").text


def test_puede_crear_una_cuenta_a_mano(app) -> None:
    """Para dar acceso a alguien sin que pase por el alta pública."""
    _como_admin(app)
    resp = app.post(
        "/admin/cuentas",
        data={"name": "Invitado", "email": "invitado@correo.com",
              "password": "clave-segura-1", "max_menus": "3"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "invitado@correo.com" in app.get("/admin/cuentas").text


def test_crear_una_cuenta_con_un_correo_repetido_avisa_y_no_duplica(app, container) -> None:
    import asyncio

    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    resp = app.post(
        "/admin/cuentas",
        data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert "error=" in resp.headers["location"]

    async def _cuantas() -> int:
        async with container.session_factory() as session:
            todas = await container.auth_repo(session).list_accounts()
            return sum(1 for c in todas if c.email == "ana@correo.com")

    assert asyncio.run(_cuantas()) == 1


def test_bloquear_a_alguien_le_impide_entrar(app) -> None:
    """El bloqueo tiene que servir de algo, no solo pintarse en la lista."""
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    app.post(f"/admin/cuentas/{_id_de(app, 'ana@correo.com')}/bloquear",
             follow_redirects=False)

    app.post("/logout", follow_redirects=False)
    entrada = app.post(
        "/login", data={"email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert entrada.status_code == 200


def test_desbloquear_la_deja_volver(app) -> None:
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    ana = _id_de(app, "ana@correo.com")
    app.post(f"/admin/cuentas/{ana}/bloquear", follow_redirects=False)
    app.post(f"/admin/cuentas/{ana}/desbloquear", follow_redirects=False)

    app.post("/logout", follow_redirects=False)
    entrada = app.post(
        "/login", data={"email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert entrada.status_code == 303


def test_puede_regalarle_mas_semanas_a_alguien(app) -> None:
    """El override manual de la cuota del BETA."""
    _como_usuaria(app, "ana@correo.com")
    _como_admin(app)
    resp = app.post(
        f"/admin/cuentas/{_id_de(app, 'ana@correo.com')}/limites",
        data={"max_menus": "5"}, follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "5" in app.get("/admin/cuentas").text


def test_un_id_que_no_es_un_id_no_tumba_la_consola(app) -> None:
    _como_admin(app)
    resp = app.post("/admin/cuentas/no-soy-un-uuid/bloquear", follow_redirects=False)
    assert resp.status_code in (303, 400)
