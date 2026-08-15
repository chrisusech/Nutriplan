"""Pesajes semanales."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import WeightEntryRow
from nutriplan.adapters.db.repositories._shared import _aware
from nutriplan.domain.models import WeightEntry


class SqlWeightRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    @staticmethod
    def _to_domain(row: WeightEntryRow) -> WeightEntry:
        week: date = row.week_start
        return WeightEntry(
            id=row.id,
            tenant_id=row.tenant_id,
            client_id=row.client_id,
            weight_kg=row.weight_kg,
            week_start=week,
            logged_at=_aware(row.logged_at),
            note=row.note,
            client_comment=row.client_comment,
        )

    async def upsert(self, entry: WeightEntry) -> WeightEntry:
        """Un pesaje por semana: si ya existe, actualiza peso y nota."""
        existing = await self.for_week(entry.client_id, entry.week_start)
        if existing is None:
            self._s.add(
                WeightEntryRow(
                    id=entry.id,
                    tenant_id=self._tenant,
                    client_id=entry.client_id,
                    weight_kg=entry.weight_kg,
                    week_start=entry.week_start,
                    logged_at=entry.logged_at,
                    note=entry.note,
                    client_comment=entry.client_comment,
                )
            )
            await self._s.flush()
            return entry
        row = await self._s.get(WeightEntryRow, existing.id)
        assert row is not None
        row.weight_kg = entry.weight_kg
        row.logged_at = entry.logged_at
        # None = "no lo mandé en esta llamada"; no borra lo que ya había escrito.
        if entry.note is not None:
            row.note = entry.note
        if entry.client_comment is not None:
            row.client_comment = entry.client_comment
        await self._s.flush()
        return self._to_domain(row)

    async def for_week(self, client_id: UUID, week_start: date) -> WeightEntry | None:
        stmt = select(WeightEntryRow).where(
            WeightEntryRow.client_id == client_id,
            WeightEntryRow.tenant_id == self._tenant,
            WeightEntryRow.week_start == week_start,
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def latest(self, client_id: UUID) -> WeightEntry | None:
        stmt = (
            select(WeightEntryRow)
            .where(
                WeightEntryRow.client_id == client_id,
                WeightEntryRow.tenant_id == self._tenant,
            )
            .order_by(WeightEntryRow.week_start.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def history(self, client_id: UUID, *, limit: int = 26) -> list[WeightEntry]:
        """El historial de pesajes, del más reciente al más viejo."""
        stmt = (
            select(WeightEntryRow)
            .where(
                WeightEntryRow.client_id == client_id,
                WeightEntryRow.tenant_id == self._tenant,
            )
            .order_by(WeightEntryRow.week_start.desc())
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def previous_before(self, client_id: UUID, week_start: date) -> WeightEntry | None:
        """El pesaje de la semana anterior a `week_start`, si hay."""
        stmt = (
            select(WeightEntryRow)
            .where(
                WeightEntryRow.client_id == client_id,
                WeightEntryRow.tenant_id == self._tenant,
                WeightEntryRow.week_start < week_start,
            )
            .order_by(WeightEntryRow.week_start.desc())
            .limit(1)
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None
