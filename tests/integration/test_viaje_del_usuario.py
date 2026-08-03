"""El viaje completo de quien descarga la app.

Se registra, se describe, recibe su menú, lo lee, califica un plato y opina.
Cada test es un paso de ese camino, no una ruta suelta.
"""

import re
import time

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app

GENERATION_TIMEOUT_S = 60.0
# Texto literal de partials/gen_error.html. Si la plantilla cambia, este test
# tiene que enterarse: un marcador que no existe convierte cada fallo en una
# espera de 60 s con un mensaje que no dice nada.
FAILURE_MARKER = "No se pudo generar el menú."


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/app.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _registrar(client: TestClient, email: str = "ana@correo.com") -> None:
    resp = client.post(
        "/registro",
        data={"name": "Ana Pérez", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _onboarding(client: TestClient, **overrides) -> None:
    data = {
        "name": "Ana Pérez", "sex": "female", "age_years": 28,
        "height_cm": 165, "weight_kg": 62, "goal": "lose_fat",
        "activity_level": "moderate",
        "meal_slots": [s.value for s in MealSlot],
        "eating_pattern_raw": "Desayuno rápido, entreno de noche.",
    }
    data.update(overrides)
    resp = client.post("/onboarding", data=data, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    # Termina en su semana, no en la consola del entrenador que ya no existe.
    assert resp.headers["location"] == "/"


def _generar(client: TestClient) -> None:
    """Lanza la generación y espera al job, como hace la pantalla."""
    started = client.post("/menu/generar")
    assert started.status_code == 200, started.text
    job = re.search(r"job=([0-9a-f-]{36})", started.text)
    assert job is not None, f"la respuesta no trae job: {started.text[:300]}"

    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        html = client.get("/menu/estado", params={"job": job.group(1), "n": 0}).text
        if "Todo listo" in html:
            return
        assert FAILURE_MARKER not in html, f"la generación falló: {html[:400]}"
        time.sleep(0.05)
    pytest.fail("la generación no terminó dentro del timeout")


# --- El camino completo -----------------------------------------------------


def test_el_marcador_de_fallo_existe_en_la_plantilla() -> None:
    """Sin esto, un job que falla no se detecta y el test espera el timeout
    entero para decir "no terminó" en vez de decir por qué."""
    from pathlib import Path

    tpl = (
        Path(__file__).resolve().parents[2]
        / "src/nutriplan/ui/web/templates/partials/gen_error.html"
    )
    assert FAILURE_MARKER in tpl.read_text(encoding="utf-8")


def test_de_registrarse_a_tener_su_menu_en_pantalla(app) -> None:
    _registrar(app)
    assert app.get("/onboarding").status_code == 200

    _onboarding(app)
    assert "Generar mi menú" in app.get("/").text

    _generar(app)
    semana = app.get("/").text
    assert "Ingredientes" in semana
    assert "Preparación" in semana
    assert semana.count('<details class="meal"') == 5


def test_quien_entra_sin_perfil_va_derecho_al_onboarding(app) -> None:
    _registrar(app)
    resp = app.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/onboarding"


def test_quien_ya_tiene_perfil_no_vuelve_a_pasar_por_el_onboarding(app) -> None:
    _registrar(app)
    _onboarding(app)
    again = app.get("/onboarding", follow_redirects=False)
    assert again.status_code == 303
    assert again.headers["location"] == "/"


def test_se_puede_mirar_cualquier_dia_de_la_semana(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    for dia in range(7):
        resp = app.get("/", params={"dia": dia})
        assert resp.status_code == 200
    # Un día fuera de rango no revienta: se acota
    assert app.get("/", params={"dia": 99}).status_code == 200


# --- El dato del BETA -------------------------------------------------------


def test_calificar_un_plato_queda_guardado(app) -> None:
    """Es el dato por el que existe este lanzamiento."""
    _registrar(app)
    _onboarding(app)
    _generar(app)

    resp = app.post(
        "/calificar", data={"dia": 0, "slot": "desayuno", "rating": 5},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert 'class="star on"' in app.get("/").text


def test_volver_a_calificar_corrige_en_vez_de_duplicar(app) -> None:
    _registrar(app)
    _onboarding(app)
    _generar(app)
    for nota in (2, 5):
        app.post("/calificar", data={"dia": 0, "slot": "desayuno", "rating": nota},
                 follow_redirects=False)
    assert app.get("/").text.count('class="star on"') == 5


def test_opinar_guarda_el_comentario_y_lo_agradece(app) -> None:
    _registrar(app)
    resp = app.post(
        "/feedback", data={"category": "idea", "message": "Me faltó variedad."},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "Gracias" in app.get("/feedback?gracias=1").text


def test_un_comentario_vacio_no_se_guarda(app) -> None:
    _registrar(app)
    resp = app.post("/feedback", data={"category": "idea", "message": "   "})
    assert resp.status_code == 200
    assert "Escríbenos algo" in resp.text


# --- Las reglas del menú ----------------------------------------------------


def test_quien_no_marca_ningun_alimento_igual_recibe_un_menu_completo(app) -> None:
    """"Sorpréndeme" es una respuesta válida: no marcar nada abre el catálogo."""
    _registrar(app)
    _onboarding(app, food_ids=[])
    _generar(app)
    assert app.get("/").text.count('<details class="meal"') == 5


def test_lo_que_alguien_dice_que_no_quiere_ver_no_aparece_en_su_menu(app) -> None:
    """Antes los dislikes se guardaban en `notes` y no los leía nadie."""
    _registrar(app)
    _onboarding(app, dislikes="huevo, atún")
    _generar(app)
    semana = " ".join(app.get("/", params={"dia": d}).text.lower() for d in range(7))
    assert "huevo" not in semana
    assert "atún" not in semana


def test_el_perfil_muestra_lo_que_la_persona_conto(app) -> None:
    _registrar(app)
    _onboarding(app, city="Medellín")
    perfil = app.get("/perfil").text
    assert "Medellín" in perfil
    assert "Desayuno rápido" in perfil
    assert "Déficit" in perfil


# --- Aislamiento ------------------------------------------------------------


def test_una_persona_no_puede_ver_el_perfil_de_otra(container) -> None:
    app = create_app(container)
    with TestClient(app) as a:
        _registrar(a, "ana@correo.com")
        _onboarding(a)
        b = TestClient(app)
        _registrar(b, "beto@correo.com")

        assert "ana@correo.com" in a.get("/perfil").text
        perfil_de_b = b.get("/perfil").text
        assert "ana@correo.com" not in perfil_de_b
        assert "Medellín" not in perfil_de_b

        # B no hereda el menú de A: sigue sin perfil
        sin_perfil = b.get("/", follow_redirects=False)
        assert sin_perfil.status_code == 303
        assert sin_perfil.headers["location"] == "/onboarding"
