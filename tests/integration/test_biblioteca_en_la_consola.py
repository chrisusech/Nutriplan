"""La biblioteca de recetas vista desde la consola.

Aquí se cierra el ciclo de la fase 3: lo que la app cocinó y la gente calificó
sube al catálogo curado que va en git, o se retira para que se vuelva a escribir.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from nutriplan.adapters.db.models import DishRatingRow, DishRecipeRow, UserRow
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.adapters.meals.recipe_catalog_store import load_recipe_catalog
from nutriplan.config.settings import Settings
from nutriplan.container import Container
from nutriplan.domain.dish_recipe import DishRecipe
from nutriplan.domain.models import MacroTargets
from nutriplan.ui.web.app import create_app

ADMIN = "admin@nutriplan.test"
CLAVE = "clave-admin-1"

CATALOGO = """version: "0.1.0"
recipes:
  - id: ya_estaba
    name_es: Algo que ya estaba
    foods: [huevo_entero]
    steps:
      - Hazlo.
"""


@pytest.fixture
def catalogo(tmp_path) -> Path:
    ruta = tmp_path / "catalog.yaml"
    ruta.write_text(CATALOGO, encoding="utf-8")
    return ruta


@pytest.fixture
def container(tmp_path, catalogo, monkeypatch) -> Container:
    monkeypatch.setattr(Settings, "branding_dir", property(lambda _s: tmp_path / "b"))
    monkeypatch.setattr(Settings, "recipes_catalog_path", property(lambda _s: catalogo))
    return Container(
        settings=Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path}/recetas.db",
            admin_email=ADMIN,
            admin_password=CLAVE,
        ),
        tenant_id=DEFAULT_TENANT_ID,
    )


@pytest.fixture
def app(container):
    with TestClient(create_app(container)) as client:
        client.post("/login", data={"email": ADMIN, "password": CLAVE}, follow_redirects=False)
        yield client


def _guardar(container: Container, *, nota: float = 4.6, votos: int = 3, **kwargs: object) -> str:
    """Deja una receta en la caché global, como si alguien se la hubiera comido."""

    async def _run() -> str:
        async with container.session_factory() as session:
            repos = container.repos(session)
            comidas = await repos.foods.list_universe()
            base: dict[str, object] = {
                "dish_key": "a" * 32,
                "template_id": "proteina_carbo_ensalada",
                "name_es": "Pollo al limón con arroz",
                "steps": ["Dora el pollo.", "Sirve con el arroz."],
                "source": "ai",
                "food_ids": [comidas[0].id],
                "reference_grams": {str(comidas[0].id): 150.0},
                "macros": MacroTargets(kcal=500, protein_g=40, carb_g=50, fat_g=12),
            }
            base.update(kwargs)
            recipe = DishRecipe(**base)  # type: ignore[arg-type]
            await container.dish_recipe_repo(session).add(
                recipe, model="modelo-de-prueba", prompt_version="v4"
            )
            # La calidad la escribe `refresh_quality` al calificar; aquí se
            # simula el resultado para no montar un plan entero.
            await session.execute(
                update(DishRecipeRow)
                .where(DishRecipeRow.dish_key == recipe.dish_key)
                .values(rating_avg=nota, rating_count=votos, times_served=votos)
            )
            await session.commit()
            return recipe.dish_key

    return asyncio.run(_run())


def _calificar_a_pelo(container: Container, dish_key: str, nota: int) -> None:
    """Una nota suelta, como las 27 del beta que se escribieron antes de que la
    calidad se recalculara sola."""

    async def _run() -> None:
        async with container.session_factory() as session:
            usuario = (
                await session.execute(select(UserRow).where(UserRow.email == ADMIN))
            ).scalar_one()
            session.add(
                DishRatingRow(
                    tenant_id=usuario.tenant_id,
                    user_id=usuario.id,
                    plan_cycle_id=uuid4(),
                    day_index=0,
                    slot="cena",
                    template_id="proteina_carbo_ensalada",
                    dish_key=dish_key,
                    rating=nota,
                    created_at=datetime.now(UTC),
                )
            )
            await session.execute(
                update(DishRecipeRow)
                .where(DishRecipeRow.dish_key == dish_key)
                .values(rating_avg=None, rating_count=0, times_served=0)
            )
            await session.commit()

    asyncio.run(_run())


def test_las_recetas_viejas_recuperan_su_nota_cuando_el_admin_recalcula(app, container) -> None:
    """141 recetas se quedaron a cero porque se calificaron antes de que la
    calidad se refrescara sola, y la consola las ordenaba por ceros."""
    clave = _guardar(container)
    _calificar_a_pelo(container, clave, nota=4)
    assert "sin calificar" in app.get("/admin/recetas").text

    resp = app.post("/admin/recetas/recalcular", follow_redirects=True)
    assert "Recalculadas 1 recetas" in resp.text
    assert "4.0" in app.get("/admin/recetas").text


def test_una_usuaria_normal_no_ve_la_biblioteca(app) -> None:
    app.post("/logout", follow_redirects=False)
    app.post(
        "/registro",
        data={"name": "Ana", "email": "ana@correo.com", "password": "clave-segura-1"},
        follow_redirects=False,
    )
    resp = app.get("/admin/recetas", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_la_biblioteca_ensena_lo_que_la_app_ha_cocinado(app, container) -> None:
    _guardar(container)
    html = app.get("/admin/recetas").text
    assert "Pollo al limón con arroz" in html
    assert "4.6" in html


def test_una_receta_que_nadie_probo_no_ofrece_promoverse(app, container) -> None:
    """El catálogo curado es lo revisado, no lo recién salido del modelo."""
    _guardar(container, nota=0.0, votos=0)
    html = app.get("/admin/recetas").text
    assert "Promover al catálogo" not in html
    assert "sin calificar" in html


def test_se_puede_mirar_solo_lo_que_escribio_la_ia(app, container) -> None:
    _guardar(container)
    assert "Pollo al limón" in app.get("/admin/recetas?origen=ai").text
    assert "Pollo al limón" not in app.get("/admin/recetas?origen=curated").text


def test_un_origen_inventado_no_rompe_el_filtro(app, container) -> None:
    _guardar(container)
    assert app.get("/admin/recetas?origen=vudu").status_code == 200


def test_promover_una_receta_la_escribe_en_el_catalogo_de_git(app, container, catalogo) -> None:
    """El YAML sigue siendo la fuente humana: pasa por git, no por la base."""
    dish_key = _guardar(container)

    resp = app.post(f"/admin/recetas/{dish_key}/promover", follow_redirects=False)
    assert resp.status_code == 303

    catalogo_leido = load_recipe_catalog(catalogo)
    assert any(r.name_es == "Pollo al limón con arroz" for r in catalogo_leido.recipes)
    assert "catálogo curado" in app.get(resp.headers["location"]).text


def test_promoverla_dos_veces_avisa_en_vez_de_duplicar(app, container, catalogo) -> None:
    dish_key = _guardar(container)
    app.post(f"/admin/recetas/{dish_key}/promover", follow_redirects=False)
    resp = app.post(f"/admin/recetas/{dish_key}/promover", follow_redirects=False)

    assert (
        "ya+estaba" in resp.headers["location"]
        or "ya estaba" in app.get(resp.headers["location"]).text
    )
    assert len(load_recipe_catalog(catalogo).recipes) == 2


def test_una_receta_vieja_sin_alimentos_no_se_promueve_a_medias(app, container, catalogo) -> None:
    """Las de antes de que existieran los macros: se avisa, no se escribe basura."""
    dish_key = _guardar(container, food_ids=[], reference_grams={}, macros=None)
    resp = app.post(f"/admin/recetas/{dish_key}/promover", follow_redirects=False)

    assert "error" in resp.headers["location"]
    assert len(load_recipe_catalog(catalogo).recipes) == 1


def test_promover_algo_que_ya_no_esta_avisa_sin_reventar(app) -> None:
    resp = app.post("/admin/recetas/fantasma/promover", follow_redirects=False)
    assert resp.status_code == 303
    assert "error" in resp.headers["location"]


def test_retirar_una_receta_la_saca_de_la_biblioteca(app, container) -> None:
    dish_key = _guardar(container, name_es="Bacalao con quinoa")
    app.post(f"/admin/recetas/{dish_key}/retirar", follow_redirects=False)
    assert "Bacalao con quinoa" not in app.get("/admin/recetas").text


def test_una_receta_retirada_deja_de_servirse_para_que_se_reescriba(app, container) -> None:
    dish_key = _guardar(container)
    app.post(f"/admin/recetas/{dish_key}/retirar", follow_redirects=False)

    async def _vive() -> bool:
        async with container.session_factory() as session:
            cache = await container.dish_recipe_repo(session).get_many([dish_key])
            return dish_key in cache

    assert asyncio.run(_vive()) is False
