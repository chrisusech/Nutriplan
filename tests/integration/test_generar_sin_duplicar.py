"""Pulsar «generar» dos veces, o dos instancias a la vez, no genera dos menús.

Es el único tramo del sistema con concurrencia de verdad, y el más caro si se
equivoca: cada lanzamiento es una tanda de llamadas facturadas al proveedor.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from nutriplan.adapters.db.models import ClientRow, GenerationJobRow
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.membership import MAX_REGENERATIONS_PER_WEEK
from nutriplan.domain.models import MealSlot
from nutriplan.domain.week import iso_week_start
from nutriplan.ui.web.app import create_app
from nutriplan.ui.web.routes import menu as menu_mod


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/dobles.db"),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def lanzamientos(monkeypatch) -> list[uuid.UUID]:
    """Los `_spawn` que de verdad ocurrieron. Sin worker: aquí se cuenta, no se genera."""
    hechos: list[uuid.UUID] = []

    def _falso_spawn(request, job_id, coro):
        coro.close()  # nadie lo va a esperar
        hechos.append(job_id)

    monkeypatch.setattr(menu_mod, "_spawn", _falso_spawn)
    return hechos


@pytest.fixture
def app(container, lanzamientos):
    with TestClient(create_app(container)) as client:
        client.post(
            "/registro",
            data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
            follow_redirects=False,
        )
        client.post(
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
        yield client


def _consulta(container, hacer):
    async def _correr():
        async with container.session_factory() as session:
            resultado = await hacer(session)
            await session.commit()
            return resultado

    return asyncio.run(_correr())


def _jobs(container) -> int:
    return _consulta(
        container,
        lambda s: s.execute(select(func.count()).select_from(GenerationJobRow)),
    ).scalar()


def _unico_job(container) -> GenerationJobRow:
    return _consulta(container, lambda s: s.execute(select(GenerationJobRow))).scalar_one()


def _perfil(container) -> tuple[uuid.UUID, uuid.UUID]:
    """El id del perfil y su tenant: cada persona es dueña del suyo."""
    fila = _consulta(
        container, lambda s: s.execute(select(ClientRow.id, ClientRow.tenant_id))
    ).one()
    return fila[0], fila[1]


def _client_id(container) -> uuid.UUID:
    return _perfil(container)[0]


def _poner_job(container, *, status: str, hace: timedelta) -> None:
    momento = datetime.now(UTC) - hace

    async def _hacer(session):
        fila = (await session.execute(select(GenerationJobRow))).scalar_one()
        fila.status = status
        fila.updated_at = momento
        return None

    _consulta(container, _hacer)


def test_pulsar_generar_dos_veces_seguidas_lanza_un_solo_menu(app, container, lanzamientos) -> None:
    """El doble clic (o el doble toque en móvil) no puede costar el doble."""
    primera = app.post("/menu/generar")
    segunda = app.post("/menu/generar")

    assert primera.status_code == segunda.status_code == 200
    assert _jobs(container) == 1
    assert len(lanzamientos) == 1


def test_un_intento_fallido_se_puede_reintentar(app, container, lanzamientos) -> None:
    """Reintentar es lo único que la persona puede hacer cuando algo se cae."""
    app.post("/menu/generar")
    _poner_job(container, status="failed", hace=timedelta(seconds=1))

    app.post("/menu/generar")
    assert _jobs(container) == 1  # el mismo job, reencolado
    assert len(lanzamientos) == 2


def test_un_menu_que_ya_se_esta_generando_no_se_lanza_otra_vez(
    app, container, lanzamientos
) -> None:
    """Con dos instancias detrás del balanceador, las dos ven el mismo job."""
    app.post("/menu/generar")
    _poner_job(container, status="running", hace=timedelta(seconds=5))

    app.post("/menu/generar")
    assert len(lanzamientos) == 1


def test_un_menu_huerfano_si_se_vuelve_a_lanzar(app, container, lanzamientos) -> None:
    """El proceso que lo tenía se cayó: pasado el margen, alguien lo recoge."""
    app.post("/menu/generar")
    _poner_job(container, status="running", hace=timedelta(hours=2))

    app.post("/menu/generar")
    assert len(lanzamientos) == 2


def test_rehacer_la_semana_sin_parar_acaba_pidiendo_esperar(app, container, lanzamientos) -> None:
    """Rehacerla no gasta saldo, pero cada intento sí gasta cuota del proveedor.

    Contar planes no sirve: al regenerar se borra el borrador anterior y siempre
    queda uno. Los intentos viven en los jobs.
    """
    client_id, tenant_id = _perfil(container)
    semana = iso_week_start()

    async def _sembrar(session):
        ahora = datetime.now(UTC)
        for i in range(MAX_REGENERATIONS_PER_WEEK + 1):
            session.add(
                GenerationJobRow(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    status="done",
                    idempotency_key=f"gen:{client_id}:{semana.isoformat()}:intento{i}",
                    created_at=ahora,
                    updated_at=ahora,
                )
            )
        return None

    _consulta(container, _sembrar)

    resp = app.post("/menu/generar")
    assert "rehiciste" in resp.text
    assert lanzamientos == []


def test_los_intentos_de_otra_semana_no_gastan_los_de_esta(app, container, lanzamientos) -> None:
    """La semana va dentro de la clave justamente para esto."""
    client_id, tenant_id = _perfil(container)
    otra = iso_week_start() - timedelta(days=7)

    async def _sembrar(session):
        ahora = datetime.now(UTC)
        for i in range(5):
            session.add(
                GenerationJobRow(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    status="done",
                    idempotency_key=f"gen:{client_id}:{otra.isoformat()}:viejo{i}",
                    created_at=ahora,
                    updated_at=ahora,
                )
            )
        return None

    _consulta(container, _sembrar)

    app.post("/menu/generar")
    assert len(lanzamientos) == 1


def test_la_clave_del_job_lleva_la_semana_dentro(app, container) -> None:
    """Sin la semana en la clave no habría de dónde contar los intentos."""
    app.post("/menu/generar")
    fila = _unico_job(container)
    esperado = f"gen:{_client_id(container)}:{iso_week_start().isoformat()}:"
    assert fila.idempotency_key.startswith(esperado)
