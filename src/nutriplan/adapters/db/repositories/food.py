"""El catálogo: alimentos globales (USDA) y propios del tenant."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    FoodRow,
)
from nutriplan.domain.food_matching import normalize
from nutriplan.domain.models import (
    FoodCategory,
    FoodItem,
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
            slot_weights={
                MealSlot(s): int(w) for s, w in (row.slot_weights or {}).items()
            },
            is_free=bool(row.is_free),
            free_text=row.free_text,
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
                existing = FoodRow(id=food.id, tenant_id=None)
                self._s.add(existing)
            self._fill_row(existing, food)
        await self._s.flush()

    async def add_custom(self, food: FoodItem) -> None:
        row = self._fill_row(FoodRow(id=food.id, tenant_id=self._tenant), food)
        self._s.add(row)
        await self._s.flush()

    def _universe_filter(self) -> ColumnElement[bool]:
        return or_(FoodRow.tenant_id.is_(None), FoodRow.tenant_id == self._tenant)

    async def get_by_ids(self, food_ids: list[UUID]) -> list[FoodItem]:
        if not food_ids:
            return []
        stmt = select(FoodRow).where(FoodRow.id.in_(food_ids), self._universe_filter())
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def list_universe(self) -> list[FoodItem]:
        stmt = select(FoodRow).where(self._universe_filter()).order_by(FoodRow.name_es)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def search(self, query: str, category: FoodCategory | None = None) -> list[FoodItem]:
        stmt = select(FoodRow).where(
            self._universe_filter(), FoodRow.name_norm.like(f"%{normalize(query)}%")
        )
        if category is not None:
            stmt = stmt.where(FoodRow.category == category.value)
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]
