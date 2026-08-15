"""Check-in semanal en el viaje real de la app."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from nutriplan.adapters.db.models import WeightEntryRow
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app


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


def _registrar(client: TestClient) -> None:
    resp = client.post(
        "/registro",
        data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def _onboarding(client: TestClient) -> None:
    resp = client.post(
        "/onboarding",
        data={
            "name": "Ana",
            "sex": "female",
            "age_years": 28,
            "height_cm": 165,
            "weight_kg": 62,
            "goal": "lose_fat",
            "activity_level": "moderate",
            "meal_slots": [s.value for s in MealSlot],
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303


def test_sin_peso_de_esta_semana_no_puede_pedir_menu_nuevo(app, container) -> None:
    _registrar(app)
    _onboarding(app)
    assert "Generar mi plan" in app.get("/").text

    # Borramos el pesaje sembrado en el onboarding → falta el de esta semana.
    async def _wipe() -> None:
        async with container.session_factory() as session:
            await session.execute(delete(WeightEntryRow))
            await session.commit()

    import asyncio

    asyncio.run(_wipe())

    home = app.get("/").text
    assert "Registrar mi peso" in home
    assert "Generar mi plan" not in home

    blocked = app.post("/menu/generar")
    assert blocked.status_code == 200
    assert "peso" in blocked.text.lower()
    assert "/check-in" in blocked.text

    form = app.get("/check-in")
    assert form.status_code == 200
    assert "weight_kg" in form.text

    saved = app.post("/check-in", data={"weight_kg": "61.5"}, follow_redirects=False)
    assert saved.status_code == 303
    assert saved.headers["location"] == "/listo"
    listo = app.get("/listo")
    assert listo.status_code == 200
    assert "61.5" in listo.text
    assert "Generar mi plan" in listo.text
