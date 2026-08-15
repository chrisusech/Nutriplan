"""Señales de gusto: lo que la IA entendió de los comentarios, por semana."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import ClientTasteSignalRow


class TasteSignals:
    """Lo acumulado de un cliente, ya deduplicado y en orden de recencia."""

    def __init__(
        self,
        avoid_food_ids: list[UUID],
        prefer_food_ids: list[UUID],
        adjustments: list[str],
    ) -> None:
        self.avoid_food_ids = avoid_food_ids
        self.prefer_food_ids = prefer_food_ids
        self.adjustments = adjustments


def _uuids(raw: list[str]) -> list[UUID]:
    """Ignora lo que no sea un UUID: el JSON es dato, no contrato."""
    out: list[UUID] = []
    for value in raw:
        try:
            out.append(UUID(str(value)))
        except ValueError:
            continue
    return out


class SqlTasteSignalRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    async def upsert(
        self,
        *,
        client_id: UUID,
        week_start: date,
        avoid_food_ids: list[UUID],
        prefer_food_ids: list[UUID],
        adjustments: list[str],
        model: str | None = None,
        source: str = "ai",
    ) -> None:
        """Una fila por semana: reenviar el cierre corrige, no duplica."""
        stmt = select(ClientTasteSignalRow).where(
            ClientTasteSignalRow.tenant_id == self._tenant,
            ClientTasteSignalRow.client_id == client_id,
            ClientTasteSignalRow.week_start == week_start,
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        avoid = [str(f) for f in avoid_food_ids]
        prefer = [str(f) for f in prefer_food_ids]
        if row is None:
            self._s.add(
                ClientTasteSignalRow(
                    id=uuid4(),
                    tenant_id=self._tenant,
                    client_id=client_id,
                    week_start=week_start,
                    avoid_food_ids=avoid,
                    prefer_food_ids=prefer,
                    adjustments=adjustments,
                    source=source,
                    model=model,
                    created_at=datetime.now(UTC),
                )
            )
        else:
            row.avoid_food_ids = avoid
            row.prefer_food_ids = prefer
            row.adjustments = adjustments
            row.source = source
            row.model = model
        await self._s.flush()

    async def accumulated_for(self, client_id: UUID, *, weeks: int = 8) -> TasteSignals:
        """Todo lo dicho en las últimas semanas, de lo más reciente a lo más viejo.

        Se acumula en vez de quedarse con la última: quien pidió "menos arroz" en
        la semana 2 no tiene por qué repetirlo cada lunes para que le hagamos caso.
        """
        stmt = (
            select(ClientTasteSignalRow)
            .where(
                ClientTasteSignalRow.tenant_id == self._tenant,
                ClientTasteSignalRow.client_id == client_id,
            )
            .order_by(ClientTasteSignalRow.week_start.desc())
            .limit(weeks)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        avoid: list[UUID] = []
        prefer: list[UUID] = []
        adjustments: list[str] = []
        for row in rows:
            avoid += _uuids(list(row.avoid_food_ids or []))
            prefer += _uuids(list(row.prefer_food_ids or []))
            adjustments += [str(a) for a in (row.adjustments or [])]
        # Lo reciente manda: dict.fromkeys conserva la primera aparición.
        return TasteSignals(
            avoid_food_ids=list(dict.fromkeys(avoid)),
            prefer_food_ids=list(dict.fromkeys(prefer)),
            adjustments=list(dict.fromkeys(adjustments))[:6],
        )
