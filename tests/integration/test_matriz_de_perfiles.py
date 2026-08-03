"""Matriz de perfiles: todos reciben menú con el motor determinista.

Lo que los unit tests no ven: alguien se registra, pide su menú y se queda
sin nada. Esta matriz es la garantía de que eso no pasa con perfiles plausibles.
"""

from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.ui.web.app import create_app

GENERATION_TIMEOUT_S = 90.0
FAILURE_MARKER = "No se pudo generar el menú."
CINCO = ["desayuno", "snack_am", "almuerzo", "snack_pm", "cena"]


PERFILES = [
    ("mujer 62 kg, déficit, 5 comidas", "female", 28, 165, 62, "lose_fat", CINCO),
    ("mujer 55 kg, déficit, 5 comidas", "female", 35, 158, 55, "lose_fat", CINCO),
    ("hombre 90 kg, volumen, 5 comidas", "male", 30, 180, 90, "gain_muscle", CINCO),
    (
        "hombre 70 kg, mantener, 4 comidas",
        "male", 45, 175, 70, "maintain",
        ["desayuno", "almuerzo", "snack_pm", "cena"],
    ),
    (
        "mujer 62 kg, déficit, 3 comidas",
        "female", 28, 165, 62, "lose_fat",
        ["desayuno", "almuerzo", "cena"],
    ),
    (
        "hombre 60 kg, volumen, 3 comidas",
        "male", 22, 170, 60, "gain_muscle",
        ["desayuno", "almuerzo", "cena"],
    ),
    (
        "mujer 75 kg, déficit fuerte, 3 comidas",
        "female", 40, 170, 75, "lose_fat",
        ["desayuno", "almuerzo", "cena"],
    ),
    (
        "hombre 100 kg, volumen, 5 comidas",
        "male", 28, 185, 100, "gain_muscle",
        CINCO,
    ),
]


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    # Sin IA: ejercemos el motor que es el camino de beta.
    return Container(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/matriz.db",
            llm_api_key="",
            llm_base_url="",
            llm_select_foods=False,
        ),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _alta(client: TestClient, email: str, perfil: tuple) -> None:
    _, sex, age, height, weight, goal, slots = perfil
    assert client.post(
        "/registro",
        data={"name": "Prueba", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    ).status_code == 303
    # Tras el alta aterriza en consentimiento; sin él no hay onboarding libre.
    cons = client.post("/consentimiento", data={"acepta": "1"}, follow_redirects=False)
    assert cons.status_code in (303, 200), cons.text
    resp = client.post(
        "/onboarding",
        data={
            "name": "Prueba", "sex": sex, "age_years": age,
            "height_cm": height, "weight_kg": weight, "goal": goal,
            "activity_level": "moderate", "meal_slots": slots,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text


def _generar(client: TestClient) -> None:
    started = client.post("/menu/generar")
    assert started.status_code == 200, started.text
    job = re.search(r"job=([0-9a-f-]{36})", started.text)
    assert job is not None, f"no arrancó: {started.text[:300]}"
    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        html = client.get("/menu/estado", params={"job": job.group(1), "n": 0}).text
        if "Todo listo" in html:
            return
        assert FAILURE_MARKER not in html, f"falló: {html[:500]}"
        time.sleep(0.05)
    pytest.fail("timeout generando")


@pytest.mark.parametrize("perfil", PERFILES, ids=[p[0] for p in PERFILES])
def test_un_perfil_plausible_recibe_su_menu(app, perfil) -> None:
    email = f"matriz-{perfil[0].replace(' ', '-').replace(',', '')}@correo.com"
    _alta(app, email, perfil)
    _generar(app)
    semana = app.get("/")
    assert semana.status_code == 200
    assert "kcal" in semana.text.lower()
