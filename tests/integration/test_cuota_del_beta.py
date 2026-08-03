"""La cuota del BETA: un menú, y el segundo se gana.

Es el trato del lanzamiento — la app es gratis y a cambio pedimos que nos
cuenten qué les pareció. Estos tests son ese trato escrito.
"""

import re
import time

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.application.quota import RATINGS_REQUIRED
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app

GENERATION_TIMEOUT_S = 60.0


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/cuota.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _con_menu(client: TestClient, email: str = "ana@correo.com") -> None:
    client.post(
        "/registro",
        data={"name": "Ana", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    )
    client.post("/consentimiento", data={"acepta": "1"}, follow_redirects=False)
    client.post(
        "/onboarding",
        data={
            "name": "Ana", "sex": "female", "age_years": 28, "height_cm": 165,
            "weight_kg": 62, "goal": "lose_fat", "activity_level": "moderate",
            "meal_slots": [s.value for s in MealSlot],
        },
        follow_redirects=False,
    )
    _generar(client)


def _generar(client: TestClient) -> None:
    started = client.post("/menu/generar")
    job = re.search(r"job=([0-9a-f-]{36})", started.text)
    assert job is not None, started.text[:300]
    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        html = client.get("/menu/estado", params={"job": job.group(1), "n": 0}).text
        if "Todo listo" in html:
            return
        time.sleep(0.05)
    pytest.fail("la generación no terminó")


def _calificar(client: TestClient, cuantos: int) -> None:
    slots = [s.value for s in MealSlot]
    puestos = 0
    for dia in range(7):
        for slot in slots:
            if puestos == cuantos:
                return
            client.post(
                "/calificar", data={"dia": dia, "slot": slot, "rating": 5},
                follow_redirects=False,
            )
            puestos += 1


def _opinar(client: TestClient) -> None:
    client.post(
        "/feedback", data={"category": "receta", "message": "Muy rico todo."},
        follow_redirects=False,
    )


def _puede_generar_otro(client: TestClient) -> bool:
    return "Generar otra semana" in client.get("/").text


# --- El trato ---------------------------------------------------------------


def test_quien_acaba_de_recibir_su_menu_no_puede_pedir_otro(app) -> None:
    _con_menu(app)
    assert not _puede_generar_otro(app)


def test_calificar_sin_opinar_no_alcanza(app) -> None:
    """Las estrellas solas no nos dicen *por qué*."""
    _con_menu(app)
    _calificar(app, RATINGS_REQUIRED)
    assert not _puede_generar_otro(app)
    assert "coment" in app.get("/").text.lower()


def test_opinar_sin_calificar_tampoco(app) -> None:
    _con_menu(app)
    _opinar(app)
    assert not _puede_generar_otro(app)


def test_quien_califica_sus_platos_y_deja_feedback_desbloquea_la_segunda_semana(
    app,
) -> None:
    """El trato completo, cumplido."""
    _con_menu(app)
    _calificar(app, RATINGS_REQUIRED)
    _opinar(app)
    assert _puede_generar_otro(app)


def test_la_segunda_semana_no_es_la_misma_que_la_primera(app, container) -> None:
    """Desbloquear y recibir el mismo menú sería una burla. La nueva reemplaza
    a la anterior —solo hay un borrador vivo— pero tiene que ser otra."""
    import asyncio

    from sqlalchemy import select

    from nutriplan.adapters.db.models import PlanCycleRow

    async def _semana() -> tuple[str, int]:
        async with container.session_factory() as session:
            fila = (
                await session.execute(select(PlanCycleRow.input_hash, PlanCycleRow.variant))
            ).all()
        assert len(fila) == 1, "debería quedar un solo borrador vivo"
        return fila[0][0], fila[0][1]

    _con_menu(app)
    primera, variante_1 = asyncio.run(_semana())

    _calificar(app, RATINGS_REQUIRED)
    _opinar(app)
    _generar(app)
    segunda, variante_2 = asyncio.run(_semana())

    assert segunda != primera
    assert variante_2 == variante_1 + 1


def test_la_cuota_no_se_salta_llamando_a_la_ruta_a_mano(app) -> None:
    """Esconder el botón no es un límite: cada menú cuesta llamadas a un
    proveedor de pago, así que lo decide el servidor."""
    _con_menu(app)
    respuesta = app.post("/menu/generar")
    assert respuesta.status_code == 200
    assert "job=" not in respuesta.text
    assert "Califica" in respuesta.text or "coment" in respuesta.text.lower()


def test_una_tercera_semana_ya_no_entra_en_el_beta(app) -> None:
    """El premio es una semana más, no barra libre."""
    _con_menu(app)
    _calificar(app, RATINGS_REQUIRED)
    _opinar(app)
    _generar(app)
    assert not _puede_generar_otro(app)
    assert "job=" not in app.post("/menu/generar").text
