"""Menús semanales, con sus días, comidas y alimentos."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    DayPlanRow,
    MealEntryRow,
    MealItemRow,
    PlanCycleRow,
)
from nutriplan.adapters.db.repositories._shared import _aware
from nutriplan.domain.errors import TenantIsolationError
from nutriplan.domain.models import (
    DayPlan,
    MacroTargets,
    MealEntry,
    MealItem,
    MealSlot,
    PlanCycle,
    PlanStatus,
)


class SqlPlanRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    @staticmethod
    def _meal_items_to_domain(rows: list[MealItemRow]) -> list[MealItem]:
        return [
            MealItem(
                id=r.id,
                food_id=r.food_id,
                recipe_id=r.recipe_id,
                grams=r.grams,
                is_free=bool(r.is_free),
                is_locked=bool(r.is_locked),
                position=r.position,
            )
            for r in rows
        ]

    @staticmethod
    def _meal_entry_to_domain(row: MealEntryRow) -> MealEntry:
        items = SqlPlanRepository._meal_items_to_domain(list(row.items)) if row.items else []
        return MealEntry(
            id=row.id,
            slot=MealSlot(row.slot),
            template_id=row.template_id,
            dish_name=row.dish_name,
            dish_key=row.dish_key,
            items=items,
            computed=MacroTargets(**row.computed),
            free_salad=row.free_salad,
            free_protein=row.free_protein,
            is_free_meal=bool(row.is_free_meal),
        )

    def _item_rows(
        self,
        items: list[MealItem],
        *,
        preserve_ids: dict[tuple[UUID | None, UUID | None], int] | None = None,
        meal_entry_id: int | None = None,
    ) -> list[MealItemRow]:
        preserve_ids = preserve_ids or {}
        rows: list[MealItemRow] = []
        for item in items:
            key = (item.food_id, item.recipe_id)
            item_id = item.id
            if item_id is None and key in preserve_ids:
                item_id = preserve_ids[key]
            row_kwargs: dict[str, Any] = {
                "tenant_id": self._tenant,
                "position": item.position,
                "food_id": item.food_id,
                "recipe_id": item.recipe_id,
                "grams": item.grams,
                "is_free": item.is_free,
                "is_locked": item.is_locked,
            }
            if meal_entry_id is not None:
                row_kwargs["meal_entry_id"] = meal_entry_id
            if item_id is not None:
                row_kwargs["id"] = item_id
            rows.append(MealItemRow(**row_kwargs))
        return rows

    def _meal_rows(self, meals: list[MealEntry]) -> list[MealEntryRow]:
        return [
            MealEntryRow(
                id=meal.id,
                tenant_id=self._tenant,
                position=i,
                slot=meal.slot.value,
                template_id=meal.template_id,
                dish_name=meal.dish_name,
                dish_key=meal.dish_key,
                computed=meal.computed.model_dump(),
                free_salad=meal.free_salad,
                free_protein=meal.free_protein,
                is_free_meal=meal.is_free_meal,
                items=self._item_rows(meal.items),
            )
            for i, meal in enumerate(meals)
        ]

    def _day_rows(self, plan: PlanCycle) -> list[DayPlanRow]:
        return [
            DayPlanRow(
                tenant_id=self._tenant,
                day_index=day.day_index,
                totals=day.totals.model_dump(),
                meals=self._meal_rows(day.meals),
            )
            for day in plan.days
        ]

    @staticmethod
    def _to_domain(row: PlanCycleRow) -> PlanCycle:
        return PlanCycle(
            id=row.id,
            tenant_id=row.tenant_id,
            client_id=row.client_id,
            targets_id=row.targets_id,
            variant=row.variant,
            version=row.version,
            days=sorted(
                (
                    DayPlan(
                        day_index=d.day_index,
                        totals=MacroTargets(**d.totals),
                        meals=[SqlPlanRepository._meal_entry_to_domain(m) for m in d.meals],
                    )
                    for d in row.days
                ),
                key=lambda d: d.day_index,
            ),
            status=PlanStatus(row.status),
            config_version=row.config_version,
            prompt_version=row.prompt_version,
            model=row.model,
            input_hash=row.input_hash,
            created_at=_aware(row.created_at),
            refined_at=_aware(row.refined_at) if row.refined_at else None,
            refine_model=row.refine_model,
            refine_prompt_version=row.refine_prompt_version,
            approved_at=_aware(row.approved_at) if row.approved_at else None,
            edited_at=_aware(row.edited_at) if row.edited_at else None,
            edit_count=row.edit_count or 0,
        )

    async def add(self, plan: PlanCycle) -> None:
        self._s.add(
            PlanCycleRow(
                id=plan.id,
                tenant_id=self._tenant,
                client_id=plan.client_id,
                targets_id=plan.targets_id,
                variant=plan.variant,
                version=plan.version,
                status=plan.status.value,
                config_version=plan.config_version,
                prompt_version=plan.prompt_version,
                model=plan.model,
                input_hash=plan.input_hash,
                created_at=plan.created_at,
                refined_at=plan.refined_at,
                refine_model=plan.refine_model,
                refine_prompt_version=plan.refine_prompt_version,
                approved_at=plan.approved_at,
                edited_at=plan.edited_at,
                edit_count=plan.edit_count,
                days=self._day_rows(plan),
            )
        )
        await self._s.flush()

    async def _row(self, plan_id: UUID) -> PlanCycleRow | None:
        stmt = select(PlanCycleRow).where(
            PlanCycleRow.id == plan_id, PlanCycleRow.tenant_id == self._tenant
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get(self, plan_id: UUID) -> PlanCycle | None:
        row = await self._row(plan_id)
        return self._to_domain(row) if row else None

    async def list_for_client(self, client_id: UUID) -> list[PlanCycle]:
        stmt = (
            select(PlanCycleRow)
            .where(PlanCycleRow.client_id == client_id, PlanCycleRow.tenant_id == self._tenant)
            .order_by(PlanCycleRow.created_at.desc())
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def find_by_input_hash(self, input_hash: str) -> list[PlanCycle]:
        stmt = select(PlanCycleRow).where(
            PlanCycleRow.input_hash == input_hash,
            PlanCycleRow.tenant_id == self._tenant,
            PlanCycleRow.edit_count == 0,
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows if r.edited_at is None]

    async def update_day(
        self,
        plan_id: UUID,
        day_index: int,
        day: DayPlan,
        *,
        mark_edited: bool = False,
    ) -> None:
        """Reescribe un solo día preservando ids de meal_entries e items que no cambiaron."""
        row = await self._row(plan_id)
        if row is None:
            raise TenantIsolationError("Plan inexistente para este tenant")

        existing = next(
            (
                d
                for d in row.days
                if d.day_index == day_index
            ),
            None,
        )
        preserve_meals: dict[MealSlot, int] = {}
        preserve_items: dict[MealSlot, dict[tuple[UUID | None, UUID | None], int]] = {}
        if existing is not None:
            for existing_meal in existing.meals:
                slot = MealSlot(existing_meal.slot)
                preserve_meals[slot] = existing_meal.id
                preserve_items[slot] = {
                    (item.food_id, item.recipe_id): item.id for item in existing_meal.items
                }

        if existing is not None:
            await self._s.delete(existing)
            await self._s.flush()

        meal_rows: list[MealEntryRow] = []
        for i, day_meal in enumerate(day.meals):
            meal_id = day_meal.id or preserve_meals.get(day_meal.slot)
            entry = MealEntryRow(
                id=meal_id,
                tenant_id=self._tenant,
                position=i,
                slot=day_meal.slot.value,
                template_id=day_meal.template_id,
                dish_name=day_meal.dish_name,
                dish_key=day_meal.dish_key,
                computed=day_meal.computed.model_dump(),
                free_salad=day_meal.free_salad,
                free_protein=day_meal.free_protein,
                is_free_meal=day_meal.is_free_meal,
                items=self._item_rows(
                    day_meal.items,
                    preserve_ids=preserve_items.get(day_meal.slot, {}),
                ),
            )
            meal_rows.append(entry)

        day_row = DayPlanRow(
            tenant_id=self._tenant,
            plan_cycle_id=plan_id,
            day_index=day_index,
            totals=day.totals.model_dump(),
            meals=meal_rows,
        )
        row.days.append(day_row)
        await self._s.flush()

        if mark_edited:
            row.edited_at = datetime.now(UTC)
            row.edit_count = (row.edit_count or 0) + 1

        await self._s.flush()

    async def count_approved_for_client(self, client_id: UUID) -> int:
        """Cuántos menús DEFINITIVOS lleva. Es el número humano de la versión:
        los borradores no consumen número, se confirma al aprobar."""
        stmt = (
            select(func.count())
            .select_from(PlanCycleRow)
            .where(
                PlanCycleRow.tenant_id == self._tenant,
                PlanCycleRow.client_id == client_id,
                PlanCycleRow.status == PlanStatus.APPROVED.value,
            )
        )
        return int((await self._s.execute(stmt)).scalar() or 0)

    async def set_version(self, plan_id: UUID, version: int) -> None:
        row = await self._row(plan_id)
        if row is None:
            raise TenantIsolationError("Plan inexistente para este tenant")
        row.version = version
        await self._s.flush()

    async def delete_draft_for_client(self, client_id: UUID) -> None:
        """Un solo borrador vivo por persona: el nuevo reemplaza al anterior.

        Se borra de las hojas hacia la raíz porque no hay cascada en la base.
        """
        drafts = (
            select(PlanCycleRow.id)
            .where(
                PlanCycleRow.tenant_id == self._tenant,
                PlanCycleRow.client_id == client_id,
                PlanCycleRow.status == PlanStatus.DRAFT.value,
            )
            .scalar_subquery()
        )
        day_ids = (
            select(DayPlanRow.id).where(DayPlanRow.plan_cycle_id.in_(drafts)).scalar_subquery()
        )
        meal_ids = (
            select(MealEntryRow.id).where(MealEntryRow.day_plan_id.in_(day_ids)).scalar_subquery()
        )
        await self._s.execute(delete(MealItemRow).where(MealItemRow.meal_entry_id.in_(meal_ids)))
        await self._s.execute(delete(MealEntryRow).where(MealEntryRow.day_plan_id.in_(day_ids)))
        await self._s.execute(delete(DayPlanRow).where(DayPlanRow.plan_cycle_id.in_(drafts)))
        await self._s.execute(delete(PlanCycleRow).where(PlanCycleRow.id.in_(drafts)))
        await self._s.flush()

    async def mark_edited(self, plan_id: UUID) -> None:
        row = await self._row(plan_id)
        if row is None:
            raise TenantIsolationError("Plan inexistente para este tenant")
        row.edited_at = datetime.now(UTC)
        row.edit_count = (row.edit_count or 0) + 1
        await self._s.flush()

    async def set_status(self, plan_id: UUID, status: PlanStatus) -> None:
        row = await self._row(plan_id)
        if row is None:
            raise TenantIsolationError("Plan inexistente para este tenant")
        row.status = status.value
        row.approved_at = datetime.now(UTC) if status == PlanStatus.APPROVED else None
        await self._s.flush()
