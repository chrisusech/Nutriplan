"""Lo que el BETA existe para averiguar, y el permiso que lo hace legítimo.

Dos cosas se prueban aquí: que sin consentimiento no se registra nada, y que
lo que sí se registra sirve para decidir.
"""

import pytest
from fastapi.testclient import TestClient

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import MealSlot
from nutriplan.ui.web.app import create_app

SUPER_EMAIL = "admin@nutriplan.test"
SUPER_PASSWORD = "super-secreta-1"


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/m.db",
            admin_email=SUPER_EMAIL,
            admin_password=SUPER_PASSWORD,
        ),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        yield client


def _registrar(client: TestClient, email: str, *, acepta: bool = True) -> None:
    resp = client.post(
        "/registro",
        data={"name": "Ana", "email": email, "password": "clave-segura-1"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, resp.text
    if acepta:
        client.post("/consentimiento", data={"acepta": "1"}, follow_redirects=False)


def _onboarding(client: TestClient, **extra) -> None:
    data = {
        "name": "Ana",
        "sex": "female",
        "age_years": 28,
        "height_cm": 165,
        "weight_kg": 62,
        "goal": "lose_fat",
        "activity_level": "moderate",
        "meal_slots": [s.value for s in MealSlot],
        "eating_pattern_raw": "Entreno de noche y ceno ligero.",
    }
    data.update(extra)
    assert client.post("/onboarding", data=data, follow_redirects=False).status_code == 303


async def _eventos(container) -> list[str]:
    from sqlalchemy import select

    from nutriplan.adapters.db.models import AppEventRow

    async with container.session_factory() as session:
        rows = (await session.execute(select(AppEventRow.name))).all()
        return [r[0] for r in rows]


# --- El permiso -------------------------------------------------------------


async def test_sin_consentimiento_no_se_registra_un_solo_evento(app, container) -> None:
    """*El* test de esta fase. Recolectar sin permiso no es recolectar: es otra
    cosa, y no la hacemos."""
    _registrar(app, "sin@permiso.co", acepta=False)
    _onboarding(app)

    assert await _eventos(container) == []


async def test_con_consentimiento_el_embudo_queda_registrado(app, container) -> None:
    _registrar(app, "con@permiso.co")
    _onboarding(app)

    eventos = await _eventos(container)
    assert "consent_given" in eventos
    assert "onboarding_done" in eventos


async def test_los_eventos_no_llevan_nada_que_identifique(app, container) -> None:
    """La tabla existe para agregarse; la PII vive en las otras."""
    from sqlalchemy import select

    from nutriplan.adapters.db.models import AppEventRow

    _registrar(app, "ana@correo.com")
    _onboarding(app)

    async with container.session_factory() as session:
        props = [r[0] for r in (await session.execute(select(AppEventRow.props))).all()]
    plano = " ".join(str(p) for p in props).lower()
    assert "ana@correo.com" not in plano
    assert "correo" not in plano


def test_un_evento_con_datos_personales_los_descarta() -> None:
    """La defensa está en el registro, no en recordar no escribirlos."""
    from nutriplan.application.analytics import _clean

    limpio = _clean({"email": "ana@correo.com", "objetivo": "lose_fat", "nombre": "Ana"})
    assert limpio == {"objetivo": "lose_fat"}


# --- El tablero -------------------------------------------------------------


def test_una_cuenta_normal_no_ve_las_metricas(app) -> None:
    _registrar(app, "curiosa@correo.com")
    resp = app.get("/admin/metricas", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_el_super_user_ve_el_embudo_y_lo_que_la_gente_escribio(app) -> None:
    _registrar(app, "ana@correo.com")
    _onboarding(app)
    app.post("/logout", follow_redirects=False)
    app.post(
        "/login",
        data={"email": SUPER_EMAIL, "password": SUPER_PASSWORD},
        follow_redirects=False,
    )

    panel = app.get("/admin/metricas")
    assert panel.status_code == 200
    assert "Activos" in panel.text
    assert "Algo falla" in panel.text
    assert "No les gusta" in panel.text
    assert "El embudo" in panel.text
    assert "Se registraron" in panel.text
    # El dato cualitativo: la pregunta con la que arrancó todo esto
    assert "Entreno de noche y ceno ligero." in panel.text


async def test_el_admin_no_se_cuenta_como_usuario_del_beta(app, container) -> None:
    """Contarse a uno mismo infla el primer paso y finge una fuga que no existe."""
    from nutriplan.adapters.db.repositories.metrics import SqlMetricsRepository

    _registrar(app, "unica@correo.com")
    _onboarding(app)

    async with container.session_factory() as session:
        embudo = await SqlMetricsRepository(session).funnel()
    assert embudo.registros == 1
    assert embudo.completaron_onboarding == 1


def test_el_embudo_solo_encadena_pasos_que_dependen_del_anterior(app, container) -> None:
    """Escribir feedback no exige haber generado un menú: meterlo en la barra
    lo haría ver como una caída."""
    from nutriplan.adapters.db.repositories.metrics import Funnel

    embudo = Funnel(
        registros=10,
        consintieron=4,
        completaron_onboarding=8,
        generaron_menu=5,
        calificaron=2,
        opinaron=9,
    )
    valores = [total for _, total in embudo.pasos]
    assert valores == sorted(valores, reverse=True)
    assert [e for e, _ in embudo.aparte] == ["Aceptaron compartir datos", "Nos escribieron"]


def _como_super(app: TestClient) -> None:
    app.post("/logout", follow_redirects=False)
    app.post(
        "/login",
        data={"email": SUPER_EMAIL, "password": SUPER_PASSWORD},
        follow_redirects=False,
    )


def test_el_super_user_se_descarga_todo_lo_que_el_beta_ha_recogido(app) -> None:
    """El CSV no es un resumen: es la materia prima para sentarse a analizar."""
    _registrar(app, "ana@correo.com")
    _onboarding(app)
    app.post(
        "/feedback",
        data={"category": "receta", "message": "El pollo se repite mucho", "nps": "8"},
        follow_redirects=False,
    )
    _como_super(app)

    csv = app.get("/admin/metricas.csv")
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    for bloque in (
        "RESUMEN",
        "PLATOS",
        "COMENTARIOS SOBRE PLATOS",
        "CIERRES DE SEMANA",
        "LO QUE PIDEN CAMBIAR",
        "OPINIONES",
        "QUIÉNES SON Y CÓMO COMEN",
        "ALIMENTOS ELEGIDOS Y QUITADOS",
        "RECETAS",
        "SEMANAS GENERADAS",
        "USO DÍA A DÍA",
    ):
        assert bloque in csv.text, f"falta el bloque {bloque}"
    assert "El pollo se repite mucho" in csv.text
    assert "Entreno de noche y ceno ligero." in csv.text


def test_lo_que_prueba_el_admin_no_ensucia_el_analisis(app) -> None:
    """El laboratorio genera menús de verdad; si entraran al CSV, cada análisis
    empezaría por descartarlos a mano."""
    _como_super(app)
    app.post(
        "/feedback",
        data={"category": "general", "message": "probando desde el laboratorio"},
        follow_redirects=False,
    )
    assert "probando desde el laboratorio" not in app.get("/admin/metricas.csv").text


def test_un_plato_con_pocos_votos_no_sale_como_el_mejor(container) -> None:
    """Con una sola calificación, una media no dice nada."""
    from nutriplan.adapters.db.repositories.metrics import MIN_RATINGS

    assert MIN_RATINGS >= 3


def test_dos_escrituras_a_la_vez_no_rompen_la_peticion(container) -> None:
    """Generar un menú tarda segundos y corre de fondo; mientras, la persona
    sigue tocando la app. Sin WAL eso era `database is locked`."""
    import asyncio

    from sqlalchemy import text

    async def _check() -> tuple[str, int]:
        async with container.session_factory() as session:
            modo = await session.execute(text("PRAGMA journal_mode"))
            espera = await session.execute(text("PRAGMA busy_timeout"))
            return modo.scalar_one(), espera.scalar_one()

    modo, espera = asyncio.run(_check())
    assert modo.lower() == "wal"
    assert espera >= 5000
