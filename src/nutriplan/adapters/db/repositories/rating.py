"""Calificaciones y feedback: el dato por el que existe el BETA."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AppFeedbackRow,
    DishRatingRow,
    MealEntryRow,
)
from nutriplan.domain.models import (
    MealSlot,
)
from nutriplan.domain.taste import RatedDish


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes naive; se asumen UTC para round-trips estables."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class SqlRatingRepository:
    """Calificaciones y feedback: el dato por el que existe el BETA."""

    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    async def for_day(self, plan_cycle_id: UUID, day_index: int) -> dict[str, dict[str, object]]:
        """Lo que ya calificó de ese día, por slot → {rating, comment}."""
        stmt = select(DishRatingRow).where(
            DishRatingRow.tenant_id == self._tenant,
            DishRatingRow.plan_cycle_id == plan_cycle_id,
            DishRatingRow.day_index == day_index,
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return {r.slot: {"rating": r.rating, "comment": r.comment} for r in rows}

    async def rate(
        self,
        *,
        user_id: UUID,
        plan_cycle_id: UUID,
        day_index: int,
        slot: MealSlot,
        rating: int,
        template_id: str | None = None,
        dish_key: str | None = None,
        comment: str | None = None,
    ) -> None:
        """Una calificación por comida: volver a puntuar corrige, no duplica.

        Upsert atómico: dos POST a la vez (estrella + comentario) no pueden
        hacer SELECT-luego-INSERT y pelearse el unique de `dish_ratings`.
        """
        dialect = self._s.bind.dialect.name if self._s.bind is not None else "sqlite"
        insert = sqlite_insert if dialect == "sqlite" else pg_insert
        text = (comment.strip() or None) if comment else None
        stmt = insert(DishRatingRow).values(
            tenant_id=self._tenant,
            user_id=user_id,
            plan_cycle_id=plan_cycle_id,
            day_index=day_index,
            slot=slot.value,
            template_id=template_id,
            dish_key=dish_key,
            rating=rating,
            comment=text,
            created_at=datetime.now(UTC),
        )
        patch: dict[str, object] = {
            "rating": stmt.excluded.rating,
            "template_id": stmt.excluded.template_id,
            "dish_key": stmt.excluded.dish_key,
        }
        # Vacío borra; None (no enviado) deja el anterior.
        if comment is not None:
            patch["comment"] = stmt.excluded.comment
        stmt = stmt.on_conflict_do_update(
            index_elements=["plan_cycle_id", "day_index", "slot"],
            set_=patch,
        )
        await self._s.execute(stmt)

    async def count_for_plan(self, plan_cycle_id: UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(DishRatingRow)
            .where(
                DishRatingRow.tenant_id == self._tenant,
                DishRatingRow.plan_cycle_id == plan_cycle_id,
            )
        )
        return int((await self._s.execute(stmt)).scalar() or 0)

    async def average_by_plan(self, plan_ids: list[UUID]) -> dict[UUID, tuple[float, int]]:
        """Nota media y número de calificaciones por semana."""
        if not plan_ids:
            return {}
        stmt = (
            select(
                DishRatingRow.plan_cycle_id,
                func.avg(DishRatingRow.rating),
                func.count(),
            )
            .where(
                DishRatingRow.tenant_id == self._tenant,
                DishRatingRow.plan_cycle_id.in_(plan_ids),
            )
            .group_by(DishRatingRow.plan_cycle_id)
        )
        rows = (await self._s.execute(stmt)).all()
        return {r[0]: (round(float(r[1]), 1), int(r[2])) for r in rows}

    async def rated_dishes(self, *, limit: int = 200) -> list[RatedDish]:
        """Todo lo que este tenant ha calificado, para deducir su gusto.

        Cruza con `meal_entries` solo por el nombre del plato: la nota vive sin
        FK al plan justamente para sobrevivirlo, así que el nombre puede faltar.
        """
        names = (
            select(
                MealEntryRow.dish_key.label("dk"),
                func.min(MealEntryRow.dish_name).label("dish_name"),
            )
            .where(MealEntryRow.tenant_id == self._tenant)
            .group_by(MealEntryRow.dish_key)
            .subquery()
        )
        stmt = (
            select(
                DishRatingRow.template_id,
                DishRatingRow.dish_key,
                names.c.dish_name,
                DishRatingRow.rating,
                DishRatingRow.comment,
            )
            .outerjoin(names, names.c.dk == DishRatingRow.dish_key)
            .where(DishRatingRow.tenant_id == self._tenant)
            .order_by(DishRatingRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            RatedDish(
                template_id=r.template_id,
                dish_key=r.dish_key,
                dish_name=r.dish_name,
                rating=int(r.rating),
                comment=r.comment,
            )
            for r in rows
        ]

    async def comments_for_plan(self, plan_cycle_id: UUID) -> list[tuple[str, int, str]]:
        """(plato, nota, comentario) de una semana: lo que la IA va a interpretar."""
        names = (
            select(
                MealEntryRow.dish_key.label("dk"),
                func.min(MealEntryRow.dish_name).label("dish_name"),
            )
            .where(MealEntryRow.tenant_id == self._tenant)
            .group_by(MealEntryRow.dish_key)
            .subquery()
        )
        stmt = (
            select(names.c.dish_name, DishRatingRow.rating, DishRatingRow.comment)
            .outerjoin(names, names.c.dk == DishRatingRow.dish_key)
            .where(
                DishRatingRow.tenant_id == self._tenant,
                DishRatingRow.plan_cycle_id == plan_cycle_id,
                DishRatingRow.comment.is_not(None),
            )
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            (r.dish_name or "un plato", int(r.rating), str(r.comment).strip())
            for r in rows
            if r.comment and str(r.comment).strip()
        ]

    async def add_feedback(
        self,
        *,
        user_id: UUID,
        category: str,
        message: str,
        nps: int | None = None,
        platform: str | None = None,
        app_version: str | None = None,
    ) -> None:
        self._s.add(
            AppFeedbackRow(
                tenant_id=self._tenant,
                user_id=user_id,
                category=category,
                message=message,
                nps=nps,
                platform=platform,
                app_version=app_version,
                created_at=datetime.now(UTC),
            )
        )
        await self._s.flush()
