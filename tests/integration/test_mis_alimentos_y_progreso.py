"""Las dos pantallas de "lo mío": la despensa y el historial.

La despensa existe porque el onboarding se responde con prisa y luego aparece
la lenteja en cuatro cenas. El historial existe porque desde que los planes se
guardan por semana hay algo que enseñar.
"""

import re
import time

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
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/mio.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )
    with TestClient(create_app(container)) as client:
        client.post(
            "/registro",
            data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
            follow_redirects=False,
        )
        yield client


def _con_perfil(app: TestClient) -> None:
    app.post(
        "/onboarding",
        data={
            "name": "Ana",
            "sex": "female",
            "age_years": "30",
            "height_cm": "165",
            "weight_kg": "62",
            "goal": "lose_fat",
            "activity_level": "moderate",
            "meal_slots": ["desayuno", "almuerzo", "cena"],
        },
        follow_redirects=False,
    )


def _un_alimento(app: TestClient) -> tuple[str, str]:
    import re

    html = app.get("/mis-alimentos").text
    dentro = html.split("Añadir")[0]
    ids = re.findall(r'name="food_id" value="([0-9a-f-]{36})"', dentro)
    assert ids, "la despensa salió vacía"
    return ids[0], html


def test_sin_perfil_la_despensa_manda_al_onboarding(app) -> None:
    resp = app.get("/mis-alimentos", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/onboarding"


def test_quien_no_marco_nada_ve_el_catalogo_entero_en_su_despensa(app) -> None:
    """Sin preferencias, «sorpréndeme» significa todo, no nada."""
    _con_perfil(app)
    html = app.get("/mis-alimentos").text
    assert "Proteínas" in html
    assert "En tu menú" in html
    assert "Ajo" in html


def test_lo_que_quito_de_su_despensa_deja_de_estar(app) -> None:
    _con_perfil(app)
    food_id, antes = _un_alimento(app)

    app.post(
        "/mis-alimentos",
        data={"food_id": food_id, "accion": "quitar"},
        follow_redirects=False,
    )

    despues = app.get("/mis-alimentos").text
    dentro = despues.split("Añadir")[0]
    assert food_id not in dentro
    assert food_id in despues, "debería poder volver a añadirlo"


def test_lo_que_quito_puede_volver_a_anadirlo(app) -> None:
    """Arrepentirse tiene que ser tan fácil como equivocarse."""
    _con_perfil(app)
    food_id, _ = _un_alimento(app)
    app.post("/mis-alimentos", data={"food_id": food_id, "accion": "quitar"})
    app.post("/mis-alimentos", data={"food_id": food_id, "accion": "anadir"})

    dentro = app.get("/mis-alimentos").text.split("Añadir")[0]
    assert food_id in dentro


def test_un_alimento_inventado_no_tumba_la_pantalla(app) -> None:
    _con_perfil(app)
    resp = app.post(
        "/mis-alimentos",
        data={"food_id": "no-soy-un-uuid", "accion": "quitar"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert app.get("/mis-alimentos").status_code == 200


def test_el_progreso_recien_empezado_invita_a_pesarse(app) -> None:
    _con_perfil(app)
    html = app.get("/progreso").text
    # El alta ya deja el peso de la primera semana: hay una fila, no un vacío.
    assert "62" in html


GENERATION_TIMEOUT_S = 60.0
FAILURE_MARKER = "No se pudo generar el menú."


def _generar(client: TestClient) -> None:
    started = client.post("/menu/generar")
    assert started.status_code == 200, started.text
    job = re.search(r"job=([0-9a-f-]{36})", started.text)
    assert job is not None, started.text[:300]
    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = client.get("/menu/estado", params={"job": job.group(1), "n": 0})
        if resp.headers.get("HX-Redirect"):
            return
        assert FAILURE_MARKER not in resp.text, resp.text[:400]
        time.sleep(0.05)
    pytest.fail("la generación no terminó dentro del timeout")


def test_el_progreso_pinta_las_kcal_de_la_semana(app) -> None:
    """Con un menú vivo se ven las tres series: peso, kcal semanales y el día a día."""
    _con_perfil(app)
    _generar(app)
    html = app.get("/progreso").text
    assert "Peso de hoy" in html
    assert "Kcal semanales" in html
    assert "Esta semana" in html
    assert "kcal" in html.lower()


def test_el_progreso_muestra_lo_que_peso_cada_semana(app) -> None:
    _con_perfil(app)
    app.post(
        "/check-in",
        data={"weight_kg": "61.2", "comentario": "Me fue bien, poca hambre"},
        follow_redirects=False,
    )
    html = app.get("/progreso").text
    assert "61.2 kg" in html


def test_sin_perfil_el_progreso_manda_al_onboarding(app) -> None:
    resp = app.get("/progreso", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/onboarding"
