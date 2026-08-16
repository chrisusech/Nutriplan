"""El catálogo con el que corren los tests.

Antes cada test leía `data/foods/curated_foods.csv`, que era la fuente de verdad
del catálogo. Ya no lo es: manda la base de datos. Lo que se lee aquí es el
artefacto de build con el que se sembró esa base la primera vez, y sirve para lo
mismo que servía el CSV — tener un catálogo real, estable y sin red de por
medio con el que ejercitar el motor.

`seed_foods` es lo que reemplaza al viejo `seed_local(session, csv)`: mete los
alimentos en la base del test. En producción eso lo hace una migración, una vez.
"""

from pathlib import Path
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.repositories import SqlFoodRepository
from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID, seed_local
from nutriplan.adapters.food.catalog import read_jsonl, to_food_item
from nutriplan.domain.models import FoodItem

BASE_CATALOG = Path(__file__).resolve().parents[2] / "data" / "foods" / "catalogo_base.jsonl"


def catalog_foods(path: Path | None = None) -> list[FoodItem]:
    """Los alimentos curados, como los ve el dominio."""
    return [to_food_item(entry) for entry in read_jsonl(path or BASE_CATALOG)]


def catalog_by_name(path: Path | None = None) -> dict[str, FoodItem]:
    return {food.name_es: food for food in catalog_foods(path)}


async def seed_foods(session: AsyncSession, tenant_id: UUID = DEFAULT_TENANT_ID) -> list[FoodItem]:
    """Tenant por defecto + catálogo, para un test que necesita base poblada."""
    await seed_local(session)
    foods = catalog_foods()
    await SqlFoodRepository(session, tenant_id).upsert_globals(foods)
    return foods
