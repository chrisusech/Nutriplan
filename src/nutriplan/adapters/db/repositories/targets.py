"""Macros calculados. Append-only: cada versión guarda su peso."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    NutritionTargetsRow,
)
from nutriplan.adapters.db.repositories._shared import _aware
from nutriplan.domain.models import (
    MacroFormula,
    MacroTargets,
    MealSlot,
    NutritionTargets,
)


class SqlTargetsRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: NutritionTargetsRow) -> NutritionTargets:
        return NutritionTargets(
            id=row.id,
            tenant_id=row.tenant_id,
            client_id=row.client_id,
            daily=MacroTargets(**row.daily),
            per_meal={MealSlot(slot): MacroTargets(**m) for slot, m in row.per_meal.items()},
            config_version=row.config_version,
            overrides=dict(row.overrides or {}),
            formula=MacroFormula(**(row.formula or {})),
            weight_kg=row.weight_kg,
            computed_at=_aware(row.computed_at),
        )

    async def add(self, targets: NutritionTargets) -> None:
        self._s.add(
            NutritionTargetsRow(
                id=targets.id,
                tenant_id=self._tenant,
                client_id=targets.client_id,
                daily=targets.daily.model_dump(),
                per_meal={s.value: m.model_dump() for s, m in targets.per_meal.items()},
                config_version=targets.config_version,
                overrides=targets.overrides,
                formula=targets.formula.model_dump(exclude_none=True),
                weight_kg=targets.weight_kg,
                computed_at=targets.computed_at,
            )
        )
        await self._s.flush()

    async def get(self, targets_id: UUID) -> NutritionTargets | None:
        stmt = select(NutritionTargetsRow).where(
            NutritionTargetsRow.id == targets_id, NutritionTargetsRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def latest_for_client(self, client_id: UUID) -> NutritionTargets | None:
        stmt = (
            select(NutritionTargetsRow)
            .where(
                NutritionTargetsRow.client_id == client_id,
                NutritionTargetsRow.tenant_id == self._tenant,
            )
            .order_by(NutritionTargetsRow.computed_at.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def history(self, client_id: UUID, *, limit: int = 52) -> list[NutritionTargets]:
        """Cada versión de los macros, de la más reciente a la más vieja."""
        stmt = (
            select(NutritionTargetsRow)
            .where(
                NutritionTargetsRow.client_id == client_id,
                NutritionTargetsRow.tenant_id == self._tenant,
            )
            .order_by(NutritionTargetsRow.computed_at.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def latest_at_weight(
        self, client_id: UUID, weight_kg: float, *, tol: float = 0.05
    ) -> NutritionTargets | None:
        """Últimos macros calculados con ese peso (p. ej. el check-in previo)."""
        stmt = (
            select(NutritionTargetsRow)
            .where(
                NutritionTargetsRow.client_id == client_id,
                NutritionTargetsRow.tenant_id == self._tenant,
                NutritionTargetsRow.weight_kg.is_not(None),
                NutritionTargetsRow.weight_kg >= weight_kg - tol,
                NutritionTargetsRow.weight_kg <= weight_kg + tol,
            )
            .order_by(NutritionTargetsRow.computed_at.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None
