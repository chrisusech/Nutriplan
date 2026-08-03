"""Calificaciones y feedback: el dato por el que existe el BETA."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AppFeedbackRow,
    DishRatingRow,
    PlanCycleRow,
)
from nutriplan.application.quota import MenuQuota, evaluate
from nutriplan.domain.models import (
    MealSlot,
)


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes naive; se asumen UTC para round-trips estables."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class SqlRatingRepository:
    """Calificaciones y feedback: el dato por el que existe el BETA."""

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    async def for_day(self, plan_cycle_id: UUID, day_index: int) -> dict[str, int]:
        """Lo que ya calificó de ese día, por slot."""
        stmt = select(DishRatingRow).where(
            DishRatingRow.tenant_id == self._tenant,
            DishRatingRow.plan_cycle_id == plan_cycle_id,
            DishRatingRow.day_index == day_index,
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return {r.slot: r.rating for r in rows}

    async def rate(
        self, *, user_id: UUID, plan_cycle_id: UUID, day_index: int, slot: MealSlot,
        rating: int, template_id: str | None = None, dish_key: str | None = None,
        comment: str | None = None,
    ) -> None:
        """Una calificación por comida: volver a puntuar corrige, no duplica."""
        stmt = select(DishRatingRow).where(
            DishRatingRow.tenant_id == self._tenant,
            DishRatingRow.plan_cycle_id == plan_cycle_id,
            DishRatingRow.day_index == day_index,
            DishRatingRow.slot == slot.value,
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is not None:
            row.rating = rating
            if comment:
                row.comment = comment
        else:
            self._s.add(
                DishRatingRow(
                    tenant_id=self._tenant, user_id=user_id,
                    plan_cycle_id=plan_cycle_id, day_index=day_index, slot=slot.value,
                    template_id=template_id, dish_key=dish_key, rating=rating,
                    comment=comment, created_at=datetime.now(UTC),
                )
            )
        await self._s.flush()

    async def count_for_plan(self, plan_cycle_id: UUID) -> int:
        stmt = select(func.count()).select_from(DishRatingRow).where(
            DishRatingRow.tenant_id == self._tenant,
            DishRatingRow.plan_cycle_id == plan_cycle_id,
        )
        return int((await self._s.execute(stmt)).scalar() or 0)

    async def has_feedback(self) -> bool:
        stmt = select(func.count()).select_from(AppFeedbackRow).where(
            AppFeedbackRow.tenant_id == self._tenant
        )
        return int((await self._s.execute(stmt)).scalar() or 0) > 0

    async def quota_for(self, plan_cycle_id: UUID, *, max_menus: int | None = None) -> MenuQuota:
        # Contar filas daría siempre 1: generar una semana nueva borra el
        # borrador anterior. `variant` sí lleva la cuenta y sobrevive en la
        # fila que queda, que es la única memoria duradera de cuántas van.
        ultimo = (
            await self._s.execute(
                select(func.max(PlanCycleRow.variant)).where(
                    PlanCycleRow.tenant_id == self._tenant
                )
            )
        ).scalar()
        menus = 0 if ultimo is None else int(ultimo) + 1
        return evaluate(
            menus_generated=menus,
            ratings=await self.count_for_plan(plan_cycle_id),
            has_feedback=await self.has_feedback(),
            max_menus=max_menus,
        )

    async def add_feedback(
        self, *, user_id: UUID, category: str, message: str,
        nps: int | None = None, platform: str | None = None,
        app_version: str | None = None,
    ) -> None:
        self._s.add(
            AppFeedbackRow(
                tenant_id=self._tenant, user_id=user_id, category=category,
                message=message, nps=nps, platform=platform, app_version=app_version,
                created_at=datetime.now(UTC),
            )
        )
        await self._s.flush()
