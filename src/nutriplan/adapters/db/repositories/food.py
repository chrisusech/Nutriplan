"""El catálogo: alimentos globales (USDA) y propios del tenant."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    FoodRow,
)
from nutriplan.domain.errors import FoodNotFoundError
from nutriplan.domain.food_matching import normalize
from nutriplan.domain.models import (
    CookingMethod,
    FoodCategory,
    FoodItem,
    FoodState,
    MealSlot,
    UnitGranularity,
)


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes naive; se asumen UTC para round-trips estables."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class SqlFoodRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: FoodRow) -> FoodItem:
        return FoodItem(
            id=row.id,
            tenant_id=row.tenant_id,
            source=row.source,
            source_ref=row.source_ref,
            name_es=row.name_es,
            name_en=row.name_en,
            category=FoodCategory(row.category),
            kcal_100g=row.kcal_100g,
            protein_100g=row.protein_100g,
            carb_100g=row.carb_100g,
            fat_100g=row.fat_100g,
            fiber_100g=row.fiber_100g or 0.0,
            tags=list(row.tags or []),
            aliases=list(row.aliases or []),
            default_unit_g=row.default_unit_g,
            unit_granularity=UnitGranularity(row.unit_granularity or "grams"),
            unit_name=row.unit_name,
            portion_step_g=row.portion_step_g,
            portion_min_g=row.portion_min_g,
            portion_max_g=row.portion_max_g,
            meal_slots=[MealSlot(s) for s in (row.meal_slots or [])],
            slot_weights={MealSlot(s): int(w) for s, w in (row.slot_weights or {}).items()},
            is_free=bool(row.is_free),
            free_text=row.free_text,
            state=FoodState(row.state or "no_aplica"),
            cooking_method=CookingMethod(row.cooking_method) if row.cooking_method else None,
            yield_factor=row.yield_factor,
            raw_equivalent_id=row.raw_equivalent_id,
            engine_default=bool(row.engine_default),
            fdc_id=row.fdc_id,
        )

    @staticmethod
    def _fill_row(row: FoodRow, food: FoodItem) -> FoodRow:
        row.source = food.source
        row.source_ref = food.source_ref
        row.name_es = food.name_es
        row.name_norm = normalize(food.name_es)
        row.name_en = food.name_en
        row.category = food.category.value
        row.kcal_100g = food.kcal_100g
        row.protein_100g = food.protein_100g
        row.carb_100g = food.carb_100g
        row.fat_100g = food.fat_100g
        row.fiber_100g = food.fiber_100g
        row.tags = list(food.tags)
        row.aliases = list(food.aliases)
        row.default_unit_g = food.default_unit_g
        row.unit_granularity = food.unit_granularity.value
        row.unit_name = food.unit_name
        row.portion_step_g = food.portion_step_g
        row.portion_min_g = food.portion_min_g
        row.portion_max_g = food.portion_max_g
        row.meal_slots = [s.value for s in food.meal_slots]
        row.slot_weights = {s.value: w for s, w in food.slot_weights.items()}
        row.is_free = food.is_free
        row.free_text = food.free_text
        row.state = food.state.value
        row.cooking_method = food.cooking_method.value if food.cooking_method else None
        row.yield_factor = food.yield_factor
        row.raw_equivalent_id = food.raw_equivalent_id
        row.engine_default = food.engine_default
        row.fdc_id = food.fdc_id
        if food.tenant_id is None:
            row.catalog_active = True
        return row

    async def upsert_globals(self, foods: list[FoodItem]) -> None:
        if not foods:
            return
        ids = [food.id for food in foods]
        stmt = select(FoodRow).where(FoodRow.id.in_(ids))
        existing_map = {row.id: row for row in (await self._s.execute(stmt)).scalars()}
        for food in foods:
            existing = existing_map.get(food.id)
            if existing is None:
                existing = FoodRow(id=food.id, tenant_id=None, catalog_active=True)
                self._s.add(existing)
            self._fill_row(existing, food)
        await self._s.flush()

    async def add_custom(self, food: FoodItem) -> None:
        row = self._fill_row(FoodRow(id=food.id, tenant_id=self._tenant), food)
        self._s.add(row)
        await self._s.flush()

    async def update(self, food: FoodItem, *, edited_by: str | None = None) -> None:
        """Corregir un alimento desde la consola. La base manda: esto persiste."""
        row = await self._s.get(FoodRow, food.id)
        if row is None:
            raise FoodNotFoundError(f"No existe el alimento {food.id}")
        self._fill_row(row, food)
        row.tenant_id = food.tenant_id
        row.catalog_active = food.tenant_id is not None or row.catalog_active
        row.curated_at = datetime.now(UTC)
        row.curated_by = edited_by
        await self._s.flush()

    async def retire(self, food_id: UUID) -> None:
        """Sale del catálogo sin borrarse: los platos viejos siguen nombrándolo."""
        await self._s.execute(
            update(FoodRow)
            .where(FoodRow.id == food_id)
            .values(catalog_active=False, engine_default=False)
        )
        await self._s.flush()

    # El catálogo tiene dos niveles y cada consulta elige el suyo:
    #
    #   _listable_filter  → lo que se LISTA (chips del perfil, pool por defecto).
    #                       Unos cientos: lo que una persona reconoce de un vistazo.
    #   _active_filter    → lo que está VIVO (miles, todo el USDA curado). No se
    #                       lista nunca; se alcanza buscándolo por nombre.
    #   _universe_filter  → todo, retirados incluidos. Solo para resolver ids de
    #                       platos ya generados, que si no perderían el nombre.

    def _listable_filter(self) -> ColumnElement[bool]:
        return or_(
            and_(
                FoodRow.tenant_id.is_(None),
                FoodRow.catalog_active.is_(True),
                FoodRow.engine_default.is_(True),
            ),
            FoodRow.tenant_id == self._tenant,
        )

    def _active_filter(self) -> ColumnElement[bool]:
        return or_(
            and_(FoodRow.tenant_id.is_(None), FoodRow.catalog_active.is_(True)),
            FoodRow.tenant_id == self._tenant,
        )

    def _universe_filter(self) -> ColumnElement[bool]:
        return or_(FoodRow.tenant_id.is_(None), FoodRow.tenant_id == self._tenant)

    async def get_by_ids(self, food_ids: list[UUID]) -> list[FoodItem]:
        if not food_ids:
            return []
        stmt = select(FoodRow).where(FoodRow.id.in_(food_ids), self._universe_filter())
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_universe(self) -> list[FoodItem]:
        """El catálogo que se le enseña a la persona. NO es la tabla entera."""
        stmt = select(FoodRow).where(self._listable_filter()).order_by(FoodRow.name_es)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def search(self, query: str, category: FoodCategory | None = None) -> list[FoodItem]:
        stmt = select(FoodRow).where(
            self._listable_filter(), FoodRow.name_norm.like(f"%{normalize(query)}%")
        )
        if category is not None:
            stmt = stmt.where(FoodRow.category == category.value)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def search_deep(
        self, query: str, category: FoodCategory | None = None, limit: int = 30
    ) -> list[FoodItem]:
        """Busca en TODO el catálogo vivo, no solo en lo que se lista.

        Es la puerta por la que un alimento del fondo llega a un plato: alguien
        pide «salmón ahumado», el código lo encuentra aquí y solo entonces se le
        enseña al modelo. El tope existe porque el resultado acaba en un enum.
        """
        norm = normalize(query).strip()
        if not norm:
            return []
        stmt = select(FoodRow).where(
            self._active_filter(),
            FoodRow.is_free.is_(False),
            FoodRow.name_norm.like(f"%{norm}%"),
        )
        if category is not None:
            stmt = stmt.where(FoodRow.category == category.value)
        # Lo listable primero: entre «pechuga de pollo» y una parte rara del
        # pollo que solo existe en USDA, gana la que la gente cocina.
        stmt = stmt.order_by(
            FoodRow.engine_default.desc(), func.length(FoodRow.name_norm), FoodRow.name_es
        ).limit(limit)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_for_console(
        self,
        *,
        query: str = "",
        category: FoodCategory | None = None,
        only_listable: bool | None = None,
        include_retired: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[FoodItem], int]:
        """Paginado para la consola. Devuelve (página, total)."""
        where = [self._universe_filter()] if include_retired else [self._active_filter()]
        if query.strip():
            where.append(FoodRow.name_norm.like(f"%{normalize(query)}%"))
        if category is not None:
            where.append(FoodRow.category == category.value)
        if only_listable is not None:
            where.append(FoodRow.engine_default.is_(only_listable))
        total = (
            await self._s.execute(select(func.count()).select_from(FoodRow).where(*where))
        ).scalar_one()
        stmt = select(FoodRow).where(*where).order_by(FoodRow.name_es).limit(limit).offset(offset)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows], int(total)
