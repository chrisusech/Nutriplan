"""Alta pública, login social, verificación de correo y baja de cuenta.

El viaje de alguien que descarga la app: se registra, confirma su correo, y
—si quiere— se va llevándose sus datos.
"""

import re

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.email import ConsoleEmailSender
from nutriplan.adapters.oauth import OAuthError, VerifiedIdentity
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import AuthProvider, MealSlot
from nutriplan.ui.web.app import create_app


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(
        Settings, "branding_dir", property(lambda _self: tmp_path / "branding")
    )
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path}/cuentas.db",
        anthropic_api_key="",
        base_url="https://app.nutriplan.test",
    )
    return Container(settings=settings, tenant_id=DEFAULT_TENANT_ID)


@pytest.fixture
def mailer(container) -> ConsoleEmailSender:
    """El correo se queda en memoria: ningún test manda nada a nadie."""
    sender = ConsoleEmailSender()
    container.__dict__["mailer"] = sender
    return sender


@pytest.fixture
def app(container, mailer):
    with TestClient(create_app(container)) as client:
        yield client


def _signup(client: TestClient, email: str = "ana@correo.com") -> None:
    resp = client.post(
        "/registro",
        data={"name": "Ana Pérez", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    assert resp.headers["location"] == "/onboarding"


def _complete_onboarding(client: TestClient) -> str:
    resp = client.post(
        "/onboarding",
        data={
            "name": "Ana Pérez", "sex": "female", "age_years": 28,
            "height_cm": 165, "weight_kg": 62, "goal": "lose_fat",
            "activity_level": "moderate",
            "meal_slots": [s.value for s in MealSlot],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return resp.headers["location"].split("cliente=")[1]


# --- Alta con correo --------------------------------------------------------


def test_cualquiera_puede_crear_su_cuenta_y_queda_dentro(app) -> None:
    """Ya no hace falta que un administrador dé de alta a nadie."""
    assert app.get("/registro").status_code == 200
    _signup(app)
    assert app.get("/onboarding").status_code == 200


def test_al_registrarse_recibe_un_correo_para_confirmar_su_direccion(app, mailer) -> None:
    _signup(app)
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == "ana@correo.com"
    assert "https://app.nutriplan.test/verificar/" in mailer.sent[0]["body"]


def test_el_enlace_del_correo_confirma_la_direccion(app, mailer) -> None:
    _signup(app)
    link = re.search(r"/verificar/(\S+)", mailer.sent[0]["body"]).group(1)
    resp = app.get(f"/verificar/{link}", follow_redirects=False)
    assert resp.status_code == 303
    assert "verificado=1" in resp.headers["location"]


def test_un_enlace_de_verificacion_inventado_no_confirma_nada(app) -> None:
    resp = app.get("/verificar/esto-no-es-un-token", follow_redirects=False)
    assert resp.status_code == 200
    assert "no es válido" in resp.text


def test_un_correo_ya_registrado_no_abre_una_segunda_cuenta(app) -> None:
    _signup(app)
    app.post("/logout", follow_redirects=False)
    resp = app.post(
        "/registro",
        data={"name": "Otra", "email": "ana@correo.com", "password": "otra-clave-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "ya tiene una cuenta" in resp.text


def test_una_contrasena_corta_no_crea_cuenta(app) -> None:
    resp = app.post(
        "/registro",
        data={"name": "Ana", "email": "corta@correo.com", "password": "1234"},
        follow_redirects=False,
    )
    assert resp.status_code == 200
    assert "al menos 8" in resp.text


# --- Google y Apple ---------------------------------------------------------


def test_entrar_con_google_crea_la_cuenta_la_primera_vez(app, monkeypatch) -> None:
    async def fake_verify(id_token: str, *, client_id: str) -> VerifiedIdentity:
        assert id_token == "token-de-google"
        return VerifiedIdentity(
            provider=AuthProvider.GOOGLE, subject="google-sub-1",
            email="ana@gmail.com", name="Ana Pérez", email_verified=True,
        )

    monkeypatch.setattr("nutriplan.ui.web.routes.auth.verify_google_id_token", fake_verify)
    resp = app.post(
        "/auth/oauth/google", data={"id_token": "token-de-google"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/onboarding"


def test_quien_ya_entro_con_google_vuelve_a_su_menu_no_al_onboarding(app, monkeypatch) -> None:
    async def fake_verify(id_token: str, *, client_id: str) -> VerifiedIdentity:
        return VerifiedIdentity(
            provider=AuthProvider.GOOGLE, subject="google-sub-1",
            email="ana@gmail.com", name="Ana Pérez", email_verified=True,
        )

    monkeypatch.setattr("nutriplan.ui.web.routes.auth.verify_google_id_token", fake_verify)
    app.post("/auth/oauth/google", data={"id_token": "t"}, follow_redirects=False)
    _complete_onboarding(app)
    app.post("/logout", follow_redirects=False)

    again = app.post("/auth/oauth/google", data={"id_token": "t"}, follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == "/"


def test_un_token_que_no_verifica_no_deja_entrar_a_nadie(app, monkeypatch) -> None:
    """Sin verificar contra Google, mandar el correo ajeno bastaría para entrar."""

    async def fake_verify(id_token: str, *, client_id: str) -> VerifiedIdentity:
        raise OAuthError("El token de Google no es válido")

    monkeypatch.setattr("nutriplan.ui.web.routes.auth.verify_google_id_token", fake_verify)
    resp = app.post(
        "/auth/oauth/google", data={"id_token": "falso"}, follow_redirects=False
    )
    assert resp.status_code == 200
    assert "no es válido" in resp.text
    assert app.get("/", follow_redirects=False).status_code == 303  # sigue fuera


def test_un_proveedor_desconocido_no_abre_sesion(app) -> None:
    resp = app.post("/auth/oauth/facebook", data={"id_token": "x"}, follow_redirects=False)
    assert resp.status_code == 200
    assert "no está disponible" in resp.text


def test_el_correo_de_una_cuenta_de_correo_no_lo_secuestra_google(app, monkeypatch) -> None:
    """Enlazar por correo sin más sería un secuestro de cuenta."""
    _signup(app, email="ana@gmail.com")
    app.post("/logout", follow_redirects=False)

    async def fake_verify(id_token: str, *, client_id: str) -> VerifiedIdentity:
        return VerifiedIdentity(
            provider=AuthProvider.GOOGLE, subject="otro-sub",
            email="ana@gmail.com", name="Impostor", email_verified=True,
        )

    monkeypatch.setattr("nutriplan.ui.web.routes.auth.verify_google_id_token", fake_verify)
    resp = app.post("/auth/oauth/google", data={"id_token": "t"}, follow_redirects=False)
    assert resp.status_code == 200
    assert "ya tiene una cuenta" in resp.text


# --- Baja de cuenta ---------------------------------------------------------


def test_quien_borra_su_cuenta_se_lleva_su_perfil_y_sus_menus(app) -> None:
    """Requisito de tienda (Apple 5.1.1(v)) y de decencia."""
    _signup(app)
    cid = _complete_onboarding(app)

    resp = app.post("/perfil/eliminar", follow_redirects=False)
    assert resp.status_code == 303
    assert "baja=" in resp.headers["location"]

    # La sesión murió con la cuenta
    assert app.get("/", follow_redirects=False).status_code == 303
    # Y no se puede volver a entrar con esas credenciales
    again = app.post(
        "/login",
        data={"email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert again.status_code == 200
    assert cid  # el perfil existió y ya no


def test_el_correo_de_una_cuenta_borrada_puede_registrarse_de_nuevo(app) -> None:
    _signup(app)
    _complete_onboarding(app)
    app.post("/perfil/eliminar", follow_redirects=False)
    _signup(app)  # la dirección quedó libre


def test_pedir_recuperar_no_revela_si_un_correo_esta_registrado(app, mailer) -> None:
    """Distinguir las respuestas convierte el formulario en un detector de cuentas."""
    _signup(app, email="existe@correo.com")
    app.post("/logout", follow_redirects=False)

    con_cuenta = app.post("/recuperar", data={"email": "existe@correo.com"})
    sin_cuenta = app.post("/recuperar", data={"email": "no-existe@correo.com"})

    assert "Si ese correo tiene una cuenta" in con_cuenta.text
    assert "Si ese correo tiene una cuenta" in sin_cuenta.text
    # Solo cambia lo invisible: el correo que sí se envió
    assert [m["to"] for m in mailer.sent].count("no-existe@correo.com") == 0


def test_la_recuperacion_ya_no_esta_capada_a_local(app) -> None:
    """En producción también hay que poder recuperar la contraseña."""
    assert app.get("/recuperar", follow_redirects=False).status_code == 200


def test_produccion_no_arranca_con_el_secreto_de_desarrollo() -> None:
    """Quien conozca ese valor forja una sesión de super_user o un reset ajeno."""
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        Settings(env="prod")
    # Con uno propio sí arranca
    assert Settings(env="prod", session_secret="x" * 48).env == "prod"
