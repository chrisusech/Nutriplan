"""Olvidé mi contraseña.

El camino completo: pedir el enlace, recibirlo, elegir una clave nueva y entrar
con ella. Y las formas de equivocarse por el camino.
"""

import re

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.email import ConsoleEmailSender
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app

VIEJA = "clave-vieja-1"
NUEVA = "clave-nueva-2"


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/reset.db",
            base_url="https://app.nutriplan.test",
        ),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def mailer(container) -> ConsoleEmailSender:
    sender = ConsoleEmailSender()
    container.__dict__["mailer"] = sender
    return sender


@pytest.fixture
def app(container, mailer):
    with TestClient(create_app(container)) as client:
        client.post(
            "/registro",
            data={"name": "Ana", "email": "ana@correo.com", "password": VIEJA},
            follow_redirects=False,
        )
        client.post("/logout", follow_redirects=False)
        yield client


def _pedir_enlace(app: TestClient, mailer, email: str = "ana@correo.com") -> str | None:
    mailer.sent.clear()
    resp = app.post("/recuperar", data={"email": email})
    assert resp.status_code == 200
    if not mailer.sent:
        return None
    enlace = re.search(r"(/recuperar/[\w.\-]+)", mailer.sent[-1]["body"])
    assert enlace is not None, mailer.sent[-1]["body"]
    return enlace.group(1)


def _entrar(app: TestClient, password: str):
    return app.post(
        "/login",
        data={"email": "ana@correo.com", "password": password},
        follow_redirects=False,
    )


# --- El camino feliz --------------------------------------------------------


def test_quien_olvido_su_clave_recibe_un_enlace_por_correo(app, mailer) -> None:
    assert _pedir_enlace(app, mailer) is not None
    assert "vence" in mailer.sent[-1]["body"]


def test_el_enlace_lleva_a_un_formulario_con_el_correo_ya_puesto(app, mailer) -> None:
    ruta = _pedir_enlace(app, mailer)
    pagina = app.get(ruta)
    assert pagina.status_code == 200
    assert "ana@correo.com" in pagina.text


def test_elegir_una_clave_nueva_deja_a_la_persona_dentro(app, mailer) -> None:
    """No tiene sentido pedirle que vuelva a escribir lo que acaba de elegir."""
    ruta = _pedir_enlace(app, mailer)
    resp = app.post(
        ruta, data={"password": NUEVA, "password_confirm": NUEVA}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_despues_de_cambiarla_la_vieja_ya_no_sirve(app, mailer) -> None:
    ruta = _pedir_enlace(app, mailer)
    app.post(ruta, data={"password": NUEVA, "password_confirm": NUEVA}, follow_redirects=False)
    app.post("/logout", follow_redirects=False)

    assert _entrar(app, VIEJA).status_code == 200  # se queda en el formulario
    assert _entrar(app, NUEVA).status_code == 303


def test_el_enlace_ya_usado_no_vuelve_a_abrir_la_cuenta(app, mailer) -> None:
    """El correo se reenvía, se comparte y se queda en el historial. Una vez
    gastado, ese enlace no puede volver a cambiar la contraseña de nadie."""
    ruta = _pedir_enlace(app, mailer)
    app.post(ruta, data={"password": NUEVA, "password_confirm": NUEVA}, follow_redirects=False)
    app.post("/logout", follow_redirects=False)

    assert "expiró o no es válido" in app.get(ruta).text
    otra = "clave-del-ladron-4"
    app.post(ruta, data={"password": otra, "password_confirm": otra}, follow_redirects=False)
    assert _entrar(app, otra).status_code == 200  # se queda en el formulario
    assert _entrar(app, NUEVA).status_code == 303


def test_pedir_un_enlace_nuevo_retira_el_que_ya_estaba_en_el_buzon(app, mailer) -> None:
    viejo = _pedir_enlace(app, mailer)
    _pedir_enlace(app, mailer)
    assert "expiró o no es válido" in app.get(viejo).text


# --- Equivocarse ------------------------------------------------------------


def test_pedirlo_para_un_correo_que_no_existe_no_lo_delata(app, mailer) -> None:
    """La respuesta es la misma exista o no la cuenta: si no, el formulario se
    convierte en un detector de correos registrados."""
    conocido = app.post("/recuperar", data={"email": "ana@correo.com"})
    desconocido = app.post("/recuperar", data={"email": "nadie@correo.com"})
    assert conocido.status_code == desconocido.status_code
    # Salvo el correo que la propia persona escribió, la página es idéntica.
    assert conocido.text.replace("ana@correo.com", "X") == desconocido.text.replace(
        "nadie@correo.com", "X"
    )


def test_si_las_dos_claves_no_coinciden_se_avisa_sin_perder_el_enlace(app, mailer) -> None:
    ruta = _pedir_enlace(app, mailer)
    resp = app.post(ruta, data={"password": NUEVA, "password_confirm": "otra-cosa-3"})
    assert resp.status_code == 200
    assert "no coinciden" in resp.text
    # El formulario sigue usable: el enlace no se gastó.
    assert (
        app.post(
            ruta, data={"password": NUEVA, "password_confirm": NUEVA}, follow_redirects=False
        ).status_code
        == 303
    )


def test_un_enlace_inventado_no_cambia_ninguna_clave(app) -> None:
    resp = app.get("/recuperar/esto-no-es-un-token")
    assert resp.status_code == 200
    assert "expiró o no es válido" in resp.text


def test_una_clave_nueva_demasiado_corta_se_rechaza(app, mailer) -> None:
    ruta = _pedir_enlace(app, mailer)
    resp = app.post(ruta, data={"password": "abc", "password_confirm": "abc"})
    assert resp.status_code == 200
    assert _entrar(app, VIEJA).status_code == 303


def test_si_el_correo_no_sale_la_persona_no_se_entera_de_por_que(app, mailer) -> None:
    """Un fallo de entrega no puede revelar si la cuenta existe."""

    async def _falla(**_kwargs):
        raise RuntimeError("SMTP caído")

    mailer.send = _falla
    resp = app.post("/recuperar", data={"email": "ana@correo.com"})
    assert resp.status_code == 200
    assert "SMTP" not in resp.text


# --- Entrar -----------------------------------------------------------------


def test_todos_los_fallos_de_login_dicen_lo_mismo(app) -> None:
    """Distinguirlos convierte el login en un detector de cuentas."""
    mal_clave = _entrar(app, "clave-que-no-es-9")
    inexistente = app.post(
        "/login",
        data={"email": "nadie@correo.com", "password": VIEJA},
        follow_redirects=False,
    )
    assert "Correo o contraseña incorrectos." in mal_clave.text
    assert "Correo o contraseña incorrectos." in inexistente.text


def test_una_cuenta_desactivada_entra_pero_solo_ve_que_su_plan_no_esta_activo(
    app, container
) -> None:
    """Su contraseña sigue siendo buena: el problema es el plan, y hay que decirlo."""
    import asyncio

    from sqlalchemy import update

    from nutriplan.adapters.db.models import UserRow

    async def _desactivar() -> None:
        async with container.session_factory() as session:
            await session.execute(
                update(UserRow).where(UserRow.email == "ana@correo.com").values(is_active=False)
            )
            await session.commit()

    asyncio.run(_desactivar())
    assert _entrar(app, VIEJA).status_code == 303
    assert app.get("/", follow_redirects=False).headers["location"] == "/plan-inactivo"


def test_salir_cierra_la_sesion_de_verdad(app, mailer) -> None:
    _entrar(app, VIEJA)
    app.post("/logout", follow_redirects=False)
    assert app.get("/", follow_redirects=False).status_code == 303
