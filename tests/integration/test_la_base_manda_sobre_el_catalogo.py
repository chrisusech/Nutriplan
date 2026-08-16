"""La base de datos es la fuente de verdad de los alimentos.

Durante mucho tiempo no lo fue: el catálogo vivía en un CSV que se volcaba a la
tabla en cada arranque, así que corregir un macro en la base no servía de nada
—al reiniciar volvía el valor del archivo— y todo lo que el archivo no listara
se apagaba solo. Estos tests son la barrera para que eso no vuelva.
"""

import inspect

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from tests.fixtures.foods import seed_foods

from nutriplan.adapters.db.migrate import upgrade_to_head_async
from nutriplan.adapters.db.repositories import SqlFoodRepository
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID, seed_local


@pytest.fixture
async def session(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    await upgrade_to_head_async(url)
    engine = create_async_engine(url)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
        await s.commit()
    await engine.dispose()


def test_el_arranque_ya_no_sabe_de_alimentos() -> None:
    """`seed_local` siembra el tenant y nada más.

    Mientras aceptara un catálogo, alguien acabaría volviendo a llamarlo en el
    arranque y las correcciones de la consola durarían hasta el siguiente
    despliegue. La firma es la barrera más barata que hay.
    """
    parametros = set(inspect.signature(seed_local).parameters) - {"session"}
    assert parametros == set(), f"seed_local volvió a aceptar datos: {parametros}"
    assert not hasattr(SqlFoodRepository, "sync_catalog_active")


async def test_la_correccion_de_un_macro_sobrevive_al_reinicio(session) -> None:
    """*El* test de todo esto.

    Alguien corrige en la consola las kcal de un alimento mal medido. Se
    reinicia la app. El valor corregido sigue ahí — antes volvía el del archivo
    en silencio y parecía que la consola no hacía nada.
    """
    await seed_foods(session)
    repo = SqlFoodRepository(session, DEFAULT_TENANT_ID)

    arroz = next(f for f in await repo.list_universe() if f.name_es == "arroz blanco cocido")
    original = arroz.kcal_100g
    arroz.kcal_100g = 999.0
    arroz.name_es = "arroz blanco de la casa"
    await repo.update(arroz, edited_by="nutricionista")

    # «Reiniciar»: es lo que corre el arranque de verdad, sin el catálogo.
    await seed_local(session)

    corregido = (await repo.get_by_ids([arroz.id]))[0]
    assert corregido.kcal_100g == 999.0 != original
    assert corregido.name_es == "arroz blanco de la casa"


async def test_renombrar_un_alimento_no_le_cambia_la_identidad(session) -> None:
    """Antes la PK salía del nombre, así que renombrar creaba otro alimento y
    dejaba huérfanas las preferencias, la despensa y los platos guardados."""
    await seed_foods(session)
    repo = SqlFoodRepository(session, DEFAULT_TENANT_ID)

    tofu = next(f for f in await repo.list_universe() if f.name_es == "tofu")
    antes = tofu.id
    tofu.name_es = "tofu firme"
    await repo.update(tofu)

    assert (await repo.get_by_ids([antes]))[0].name_es == "tofu firme"
    nombres = [f.name_es for f in await repo.list_universe()]
    assert nombres.count("tofu firme") == 1
    assert "tofu" not in nombres  # no quedó el viejo conviviendo con el nuevo


async def test_un_alimento_nuevo_se_da_de_alta_sin_tocar_el_codigo(session) -> None:
    """La arepa de una marca local no está en USDA y tiene que poder entrar."""
    from nutriplan.adapters.food.catalog import CatalogEntry, to_food_item
    from nutriplan.domain.models import FoodCategory, MealSlot

    await seed_foods(session)
    repo = SqlFoodRepository(session, DEFAULT_TENANT_ID)
    nueva = to_food_item(
        CatalogEntry(
            source="curated",
            name_es="bocadillo veleño",
            category=FoodCategory.CARB,
            kcal_100g=290,
            protein_100g=0.5,
            carb_100g=72,
            fat_100g=0.1,
            meal_slots=[MealSlot.SNACK_AM],
            engine_default=True,
        )
    )
    await repo.upsert_globals([nueva])

    assert "bocadillo veleño" in {f.name_es for f in await repo.list_universe()}
    assert [f.name_es for f in await repo.search("bocadillo")] == ["bocadillo veleño"]
