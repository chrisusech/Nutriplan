"""El bucle semanal completo, tal como lo vive la persona.

Genera su semana, la vive, la cierra (peso + cinco estrellas) y solo entonces
la app le arma la siguiente. El comentario suma, pero no es puerta.
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
SLOTS = [s.value for s in MealSlot]


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/semana.db"),
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
        client.post(
            "/onboarding",
            data={
                "name": "Ana",
                "sex": "female",
                "age_years": "30",
                "height_cm": "165",
                "weight_kg": "62",
                "goal": "lose_fat",
                "activity_level": "moderate",
                "meal_slots": SLOTS,
            },
            follow_redirects=False,
        )
        yield client


def _generar(client: TestClient) -> None:
    started = client.post("/menu/generar")
    job = re.search(r"job=([0-9a-f-]{36})", started.text)
    assert job is not None, started.text[:300]
    deadline = time.monotonic() + GENERATION_TIMEOUT_S
    while time.monotonic() < deadline:
        resp = client.get("/menu/estado", params={"job": job.group(1), "n": 0})
        if resp.headers.get("HX-Redirect", "").startswith("/compra"):
            return
        assert "No se pudo generar el menú." not in resp.text, resp.text[:400]
        time.sleep(0.05)
    pytest.fail("la generación no terminó dentro del timeout")


def _calificar(client: TestClient, n: int = 5) -> None:
    slots = ["desayuno", "snack_am", "almuerzo", "snack_pm", "cena"]
    for slot in slots[:n]:
        resp = client.post(
            "/calificar",
            data={"dia": "0", "slot": slot, "rating": "5"},
            follow_redirects=False,
        )
        assert resp.status_code == 303, resp.text[:300]


# --- Lo que se pide para cerrar ---------------------------------------------


def test_la_primera_semana_se_genera_solo_con_el_peso_del_alta(app) -> None:
    """Nadie puede calificar platos que todavía no ha comido."""
    assert "Generar mi plan" in app.get("/").text
    _generar(app)
    assert app.get("/").text.count('<details class="meal"') == 5


def test_con_la_semana_ya_generada_el_cierre_pide_cinco_estrellas(app) -> None:
    _generar(app)
    html = app.get("/check-in").text
    assert "Calificar al menos 5 platos" in html
    assert "required" not in html.split("comentario")[1][:200]
    assert "Revisa tus alimentos" in html
    assert "kcal esta semana" in html


def test_no_puede_pedir_otra_semana_sin_cerrar_la_que_vive(app) -> None:
    _generar(app)
    bloqueada = app.post("/menu/generar")
    assert bloqueada.status_code == 200
    assert "calificar al menos 5 platos" in bloqueada.text.lower()
    assert 'href="/check-in"' in bloqueada.text


def test_con_cuatro_estrellas_no_se_cierra(app) -> None:
    _generar(app)
    _calificar(app, n=4)
    resp = app.post("/check-in", data={"weight_kg": "61.4", "comentario": ""})
    assert resp.status_code == 200
    assert "califica al menos 5" in resp.text.lower()


def test_cinco_estrellas_y_peso_cierran_sin_comentario(app) -> None:
    _generar(app)
    _calificar(app, n=5)
    resp = app.post(
        "/check-in",
        data={"weight_kg": "61.4", "comentario": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/listo"


def test_un_peso_imposible_no_cierra_la_semana(app) -> None:
    _generar(app)
    _calificar(app)
    resp = app.post(
        "/check-in",
        data={"weight_kg": "900", "comentario": "Me fue bien, con energía"},
    )
    assert resp.status_code == 200
    assert "peso" in resp.text.lower()


def test_al_cerrar_la_semana_espera_al_domingo(app) -> None:
    """Cerrar no regenera: el domingo arma la siguiente."""
    _generar(app)
    _calificar(app)

    cerrada = app.post(
        "/check-in",
        data={
            "weight_kg": "61.4",
            "comentario": "Me fue bien, aunque el pescado no lo repetiría",
        },
        follow_redirects=False,
    )
    assert cerrada.status_code == 303
    assert cerrada.headers["location"] == "/listo"
    listo = app.get("/listo").text
    assert "El domingo preparamos tu siguiente menú" in listo
    assert "Generar mi plan" not in listo
    semana = app.get("/").text
    assert "Generar otra semana" not in semana
    assert "El domingo preparamos tu siguiente menú" in semana


def test_lo_que_escribio_queda_guardado_para_la_semana_siguiente(app) -> None:
    _generar(app)
    _calificar(app)
    app.post(
        "/check-in",
        data={"weight_kg": "61.4", "comentario": "El pescado no lo repetiría"},
    )
    assert "El pescado no lo repetiría" in app.get("/check-in").text
    assert "El pescado no lo repetiría" in app.get("/progreso").text


def test_cerrar_no_deja_regenerar_esta_semana_a_mano(app) -> None:
    """El botón era una trampa: rehace el menú que todavía se come."""
    _generar(app)
    _calificar(app)
    app.post("/check-in", data={"weight_kg": "61.4", "comentario": "Todo bien esta semana"})
    bloqueada = app.post("/menu/generar")
    assert "domingo" in bloqueada.text.lower()


def test_el_tick_del_domingo_arma_la_siguiente_si_ya_cerro(app, container) -> None:
    """Cerró, tiene saldo, y el domingo no toca el menú que todavía come."""
    import asyncio
    from datetime import datetime, timedelta

    from nutriplan.application.auto_week import run_auto_week
    from nutriplan.application.membership import grant_weeks
    from nutriplan.domain.auto_week import BOGOTA
    from nutriplan.domain.week import iso_week_start

    _generar(app)
    _calificar(app)
    app.post("/check-in", data={"weight_kg": "61.4", "comentario": "Todo bien esta semana"})

    async def _saldo_y_tick() -> tuple[int, object, object]:
        async with container.session_factory() as session:
            auth = container.auth_repo(session)
            account = await auth.get_by_email_any_provider("ana@correo.com")
            assert account is not None
            await grant_weeks(account=account, memberships=container.membership_repo(session))
            await session.commit()
        today = datetime.now(BOGOTA).date()
        sunday = today + timedelta(days=(6 - today.weekday()))
        when = datetime(sunday.year, sunday.month, sunday.day, 19, 0, tzinfo=BOGOTA)
        n = await run_auto_week(container, when=when, force=True)
        cerrada = iso_week_start(today)
        objetivo = cerrada + timedelta(days=7)
        return n, cerrada, objetivo

    n, esta, siguiente = asyncio.run(_saldo_y_tick())
    assert n == 1

    async def _planes() -> tuple[object, object]:
        async with container.session_factory() as session:
            account = await container.auth_repo(session).get_by_email_any_provider("ana@correo.com")
            assert account is not None
            repos = container.repos(session, account.tenant_id)
            client = await repos.clients.get_by_user(account.id)
            assert client is not None
            activo = await repos.plans.get(client.active_plan_id) if client.active_plan_id else None
            proximo = await repos.plans.for_week(client.id, siguiente)
            return (activo.week_start if activo else None, proximo.week_start if proximo else None)

    vigente, creado = asyncio.run(_planes())
    assert vigente == esta
    assert creado == siguiente


def test_sin_cerrar_el_tick_no_inventa_un_menu(app, container) -> None:
    import asyncio
    from datetime import datetime, timedelta

    from nutriplan.application.auto_week import run_auto_week
    from nutriplan.domain.auto_week import BOGOTA

    _generar(app)
    today = datetime.now(BOGOTA).date()
    sunday = today + timedelta(days=(6 - today.weekday()))
    when = datetime(sunday.year, sunday.month, sunday.day, 19, 0, tzinfo=BOGOTA)
    assert asyncio.run(run_auto_week(container, when=when, force=True)) == 0
