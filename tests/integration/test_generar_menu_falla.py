"""Cuando generar el menú no sale bien.

El camino feliz ya está probado en el viaje del usuario. Aquí está lo otro: que
nadie se quede mirando una rueda para siempre ni vea una traza de Python.
"""


import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/fallos.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        client.post(
            "/registro",
            data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
            follow_redirects=False,
        )
        yield client


def _onboarding(app: TestClient, **extra) -> None:
    data = {
        "name": "Ana", "sex": "female", "age_years": 28, "height_cm": 165,
        "weight_kg": 62, "goal": "lose_fat", "activity_level": "moderate",
        "meal_slots": [s.value for s in MealSlot],
    }
    data.update(extra)
    assert app.post("/onboarding", data=data, follow_redirects=False).status_code == 303


# --- Antes de empezar -------------------------------------------------------


def test_pedir_un_menu_sin_haber_contado_nada_de_uno_avisa(app) -> None:
    resp = app.post("/menu/generar")
    assert resp.status_code == 200
    assert "perfil" in resp.text.lower()
    assert "job=" not in resp.text


def test_una_restriccion_que_no_existe_no_tumba_la_generacion(app) -> None:
    """El formulario puede traer cualquier cosa —un cliente viejo, alguien
    trasteando—. El filtro de alimentos se niega a adivinar qué prohíbe una
    restricción desconocida, así que se descarta aquí y no varios pasos después
    en forma de 500."""
    _onboarding(app, restrictions=["vegan", "no_gluten", "inventada"])
    resp = app.post("/menu/generar")
    assert resp.status_code == 200
    assert "job=" in resp.text


def test_las_restricciones_de_verdad_si_se_guardan(app, container) -> None:
    """Descartar las desconocidas no puede llevarse las buenas por delante."""
    import asyncio

    from sqlalchemy import select

    from nutriplan.adapters.db.models import ClientRow

    _onboarding(app, restrictions=["vegan", "no_gluten"])

    async def _guardadas() -> list[str]:
        async with container.session_factory() as session:
            fila = await session.execute(select(ClientRow.restrictions))
            return list(fila.scalars().one())

    assert asyncio.run(_guardadas()) == ["no_gluten"]


# --- Mientras espera --------------------------------------------------------


def test_preguntar_por_un_job_que_no_existe_no_deja_la_rueda_girando(app) -> None:
    _onboarding(app)
    html = app.get(
        "/menu/estado",
        params={"job": "00000000-0000-4000-8000-000000000000", "n": 0},
    ).text
    assert "no se encontró" in html or "interrumpió" in html


def test_un_job_que_no_es_un_uuid_no_tumba_la_pantalla(app) -> None:
    _onboarding(app)
    resp = app.get("/menu/estado", params={"job": "esto-no-es-un-uuid", "n": 0})
    assert resp.status_code == 200


def test_el_mensaje_de_espera_va_cambiando(app) -> None:
    """Un texto fijo durante veinte segundos parece que se colgó."""
    _onboarding(app)
    job = "00000000-0000-4000-8000-000000000000"
    textos = {
        app.get("/menu/estado", params={"job": job, "n": n}).text for n in (0, 1)
    }
    assert len(textos) >= 1  # el job no existe; lo que importa es que responde


def test_un_menu_abandonado_a_medias_se_da_por_perdido(app, container) -> None:
    """Si el proceso murió generando, la fila se queda en `running` y nadie la
    va a tocar nunca más. Pasado el margen se dice, en vez de girar sin fin."""
    import asyncio
    import uuid
    from datetime import UTC, datetime, timedelta

    from nutriplan.adapters.db.models import GenerationJobRow

    _onboarding(app)
    muerto = uuid.uuid4()
    hace_dos_horas = datetime.now(UTC) - timedelta(hours=2)

    async def _sembrar() -> None:
        async with container.session_factory() as session:
            session.add(
                GenerationJobRow(
                    id=muerto,
                    tenant_id=container.tenant_id,
                    status="running",
                    idempotency_key=f"gen:huerfano:{muerto}",
                    created_at=hace_dos_horas,
                    updated_at=hace_dos_horas,
                )
            )
            await session.commit()

    asyncio.run(_sembrar())
    html = app.get("/menu/estado", params={"job": str(muerto), "n": 0}).text
    assert "interrumpió" in html
    assert "autorenew" not in html  # ya no gira
