"""Marcar la nevera antes de generar, y aterrizar en la compra al terminar.

Dos cambios de flujo que se sostienen entre sí: lo primero que se hace con una
semana recién hecha es comprarla, y lo que ya está comprado no debería volver a
la lista. Los tests recorren ese camino como lo recorre una persona, porque cada
eslabón que se olvide (la pantalla, la caché del plan, la lista) hace que la
función parezca rota en silencio.
"""

import re
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app

GENERATION_TIMEOUT_S = 60.0
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


@pytest.fixture
def con_perfil(app):
    """Alguien registrado y descrito, justo antes de generar su primera semana."""
    resp = app.post(
        "/registro",
        data={"name": "Ana Pérez", "email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    resp = app.post(
        "/onboarding",
        data={
            "name": "Ana Pérez",
            "sex": "female",
            "age_years": 28,
            "height_cm": 165,
            "weight_kg": 62,
            "goal": "lose_fat",
            "activity_level": "moderate",
            "meal_slots": [s.value for s in MealSlot],
            "eating_pattern_raw": "Desayuno rápido, entreno de noche.",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    return app


def _generar(client: TestClient) -> str:
    """Genera la semana y devuelve a dónde manda la app al terminar."""
    started = client.post("/menu/generar")
    assert started.status_code == 200, started.text
    job = re.search(r"job=([0-9a-f-]{36})", started.text)
    assert job is not None, started.text[:300]

    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = client.get("/menu/estado", params={"job": job.group(1), "n": 0})
        destino = resp.headers.get("HX-Redirect")
        if destino:
            return destino
        assert FAILURE_MARKER not in resp.text, resp.text[:400]
        time.sleep(0.05)
    pytest.fail("la generación no terminó dentro del timeout")


def _casillas(html: str) -> list[str]:
    return re.findall(r'name="food_id" value="([0-9a-f-]{36})"', html)


# --- Antes de generar: la nevera --------------------------------------------


def test_desde_su_semana_puede_decir_lo_que_ya_tiene_en_casa(con_perfil) -> None:
    assert "/tengo-en-casa" in con_perfil.get("/").text

    pantalla = con_perfil.get("/tengo-en-casa")
    assert pantalla.status_code == 200
    assert "Lo que tengo en casa" in pantalla.text
    assert len(_casillas(pantalla.text)) > 10


def test_lo_que_marca_sigue_marcado_cuando_vuelve(con_perfil) -> None:
    ids = _casillas(con_perfil.get("/tengo-en-casa").text)[:2]
    guardado = con_perfil.post("/tengo-en-casa", data={"food_id": ids}, follow_redirects=False)
    assert guardado.status_code == 303
    # Tras el alta la semana ya está cerrada: vuelve a generar, no a un menú viejo.
    assert guardado.headers["location"] == "/listo"

    de_vuelta = con_perfil.get("/tengo-en-casa").text
    assert "2 marcados" in de_vuelta
    for food_id in ids:
        assert re.search(rf'value="{food_id}"\s+checked', de_vuelta), f"{food_id} sin marcar"
    assert "2 cosas en casa" in con_perfil.get("/").text


def test_desde_generar_la_despensa_vuelve_a_listo(con_perfil) -> None:
    ids = _casillas(con_perfil.get("/tengo-en-casa?volver=/listo").text)[:1]
    assert 'name="volver" value="/listo"' in con_perfil.get("/tengo-en-casa?volver=/listo").text
    guardado = con_perfil.post(
        "/tengo-en-casa",
        data={"food_id": ids, "volver": "/listo"},
        follow_redirects=False,
    )
    assert guardado.status_code == 303
    assert guardado.headers["location"] == "/listo"


def test_no_se_puede_meter_en_la_nevera_algo_que_no_esta_en_su_menu(con_perfil) -> None:
    """La casilla sale de su menú, pero el formulario lo manda el navegador."""
    con_perfil.post("/tengo-en-casa", data={"food_id": [str(uuid4())]}, follow_redirects=False)
    assert "0 marcados" in con_perfil.get("/tengo-en-casa").text


def test_lo_marcado_llega_hasta_la_semana_generada(con_perfil) -> None:
    """El eslabón largo: pantalla → tabla → motor → menú. Con la nevera entera
    marcada, el menú tiene que salir igualmente y estar hecho con lo de casa."""
    ids = _casillas(con_perfil.get("/tengo-en-casa").text)
    con_perfil.post("/tengo-en-casa", data={"food_id": ids})
    _generar(con_perfil)

    semana = con_perfil.get("/").text
    assert semana.count('<details class="meal"') == 5


# --- Después de generar: la compra ------------------------------------------


def test_al_terminar_de_generar_se_aterriza_en_la_compra(con_perfil) -> None:
    assert _generar(con_perfil) == "/compra?nueva=1"

    compra = con_perfil.get("/compra?nueva=1")
    assert compra.status_code == 200
    assert "Tu semana está lista" in compra.text
    assert "Ver mi menú" in compra.text
    # Y desde ahí, al menú: la compra se mira una vez, la semana todos los días.
    assert "Ingredientes" in con_perfil.get("/").text


def test_la_compra_no_te_manda_a_comprar_lo_que_ya_tienes(con_perfil) -> None:
    """Con la nevera entera marcada, la compra no puede pedir nada."""
    ids = _casillas(con_perfil.get("/tengo-en-casa").text)
    con_perfil.post("/tengo-en-casa", data={"food_id": ids})
    _generar(con_perfil)

    compra = con_perfil.get("/compra").text
    # Las líneas siguen ahí, marcadas: hay que poder ver de dónde salen los
    # gramos de la semana.
    assert "ya lo tienes" in compra
    assert 'type="checkbox"' not in compra, "no hay nada que tachar si no compras nada"
    assert "0 ítems" in compra, "sigue mandando a comprar algo"
