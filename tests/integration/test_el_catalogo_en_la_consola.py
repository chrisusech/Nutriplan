"""El catálogo de alimentos, corregido desde la consola.

Es la pantalla que cierra el cambio: mientras el catálogo vivía en un archivo,
tener una tabla en la base no servía de nada porque nadie podía escribir en
ella. Aquí se comprueba que corregir un macro, dar de alta un alimento que no
está en USDA y retirar uno son cosas que se hacen con el ratón y se quedan.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.foods import catalog_foods

from nutriplan.adapters.db.repositories import SqlFoodRepository
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.models import FoodItem
from nutriplan.ui.web.app import create_app

ADMIN = "admin@nutriplan.test"
CLAVE = "clave-admin-1"


@pytest.fixture
def container(tmp_path, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    return Container(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/alimentos.db",
            admin_email=ADMIN,
            admin_password=CLAVE,
        ),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        _sembrar(container)
        client.post("/login", data={"email": ADMIN, "password": CLAVE}, follow_redirects=False)
        yield client


def _sembrar(container: Container) -> None:
    """El catálogo curado, que es con lo que la consola va a trabajar."""

    async def _run() -> None:
        async with container.session_factory() as session:
            await SqlFoodRepository(session, DEFAULT_TENANT_ID).upsert_globals(catalog_foods())
            await session.commit()

    asyncio.run(_run())


def _leer(container: Container, nombre: str) -> FoodItem | None:
    async def _run() -> FoodItem | None:
        async with container.session_factory() as session:
            repo = SqlFoodRepository(session, DEFAULT_TENANT_ID)
            encontrados = await repo.search_deep(nombre)
            return next((f for f in encontrados if f.name_es == nombre), None)

    return asyncio.run(_run())


def _csrf(html: str) -> str:
    marca = 'name="_csrf" value="'
    inicio = html.index(marca) + len(marca)
    return html[inicio : html.index('"', inicio)]


def test_la_consola_no_vomita_miles_de_alimentos_de_golpe(app) -> None:
    """Se busca, no se navega: la página es paginada por diseño."""
    página = app.get("/admin/alimentos")
    assert página.status_code == 200
    assert página.text.count("admin-row") <= 50


def test_la_nutricionista_corrige_un_macro_y_sobrevive_al_reinicio(app, container) -> None:
    """*El* test de todo esto: la corrección se queda en la base."""
    página = app.get("/admin/alimentos?q=arroz+blanco")
    arroz = _leer(container, "arroz blanco cocido")
    assert arroz is not None and arroz.kcal_100g != 141

    respuesta = app.post(
        "/admin/alimentos/guardar",
        data={
            "_csrf": _csrf(página.text),
            "food_id": str(arroz.id),
            "name_es": arroz.name_es,
            "source": arroz.source,
            "fdc_id": str(arroz.fdc_id or ""),
            "category": arroz.category.value,
            "kcal_100g": "141",
            "protein_100g": str(arroz.protein_100g),
            "carb_100g": str(arroz.carb_100g),
            "fat_100g": str(arroz.fat_100g),
            "fiber_100g": str(arroz.fiber_100g),
            "state": arroz.state.value,
            "yield_factor": str(arroz.yield_factor or ""),
            "meal_slots": ";".join(s.value for s in arroz.meal_slots),
            "unit_granularity": arroz.unit_granularity.value,
            "engine_default": "1",
        },
        follow_redirects=False,
    )
    assert respuesta.status_code == 303

    corregido = _leer(container, "arroz blanco cocido")
    assert corregido is not None and corregido.kcal_100g == 141


def test_un_macro_imposible_no_entra_ni_escrito_a_mano(app, container) -> None:
    """El formulario pasa por los mismos controles que el volcado de USDA."""
    página = app.get("/admin/alimentos")
    respuesta = app.post(
        "/admin/alimentos/guardar",
        data={
            "_csrf": _csrf(página.text),
            "source": "curated",
            "name_es": "invento imposible",
            "category": "carb",
            "kcal_100g": "10",  # con 70 g de carbo, no cuadra ni de lejos
            "protein_100g": "0",
            "carb_100g": "70",
            "fat_100g": "0",
            "meal_slots": "almuerzo",
            "unit_granularity": "grams",
        },
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    assert "error=" in respuesta.headers["location"]
    assert _leer(container, "invento imposible") is None


def test_se_da_de_alta_un_alimento_que_no_esta_en_usda(app, container) -> None:
    """La arepa de una marca local o el bocadillo veleño no están en USDA."""
    página = app.get("/admin/alimentos")
    respuesta = app.post(
        "/admin/alimentos/guardar",
        data={
            "_csrf": _csrf(página.text),
            "source": "curated",
            "name_es": "bocadillo veleño",
            "category": "carb",
            "kcal_100g": "290",
            "protein_100g": "0.5",
            "carb_100g": "72",
            "fat_100g": "0.1",
            "meal_slots": "snack_am",
            "unit_granularity": "grams",
            "engine_default": "1",
        },
        follow_redirects=False,
    )
    assert respuesta.status_code == 303
    creado = _leer(container, "bocadillo veleño")
    assert creado is not None and creado.kcal_100g == 290


def test_retirar_un_alimento_lo_saca_del_catalogo_pero_no_lo_borra(app, container) -> None:
    página = app.get("/admin/alimentos?q=tofu")
    tofu = _leer(container, "tofu")
    assert tofu is not None

    app.post(
        "/admin/alimentos/retirar",
        data={"_csrf": _csrf(página.text), "food_id": str(tofu.id)},
        follow_redirects=False,
    )

    async def _sigue_resolviendo() -> bool:
        async with container.session_factory() as session:
            repo = SqlFoodRepository(session, DEFAULT_TENANT_ID)
            return bool(await repo.get_by_ids([tofu.id]))

    assert _leer(container, "tofu") is None  # fuera del catálogo
    assert asyncio.run(_sigue_resolviendo())  # pero la fila se queda
