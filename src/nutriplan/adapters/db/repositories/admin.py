"""La vista del super_user: todas las cuentas, en una sola consulta.

Cruza tenants a propósito, como `metrics`. La diferencia es que aquí sí salen
datos de personas concretas, así que la ruta que lo usa está detrás del guard de
`/admin`, que revalida el rol contra la base y no se fía de la cookie.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    ClientRow,
    NutritionTargetsRow,
    PlanCycleRow,
    UserRow,
    WeightEntryRow,
)
from nutriplan.adapters.db.repositories._shared import _aware
from nutriplan.domain.models import Role
from nutriplan.domain.week_close import MIN_COMMENT_CHARS


@dataclass(frozen=True)
class AccountOverview:
    """Una fila del listado: quién es y en qué punto está."""

    user_id: UUID
    tenant_id: UUID
    client_id: UUID | None
    name: str
    email: str
    is_active: bool
    created_at: datetime
    weight_kg: float | None
    goal: str | None
    kcal: int | None
    weeks_generated: int
    last_week: date | None
    last_checkin: date | None

    @property
    def has_profile(self) -> bool:
        return self.client_id is not None


@dataclass(frozen=True)
class AutoWeekCandidate:
    """Alguien que cerró la semana y todavía no tiene el menú siguiente."""

    client_id: UUID
    tenant_id: UUID
    user_id: UUID


class SqlAdminRepository:
    """Sin filtro de tenant: es el único punto del sistema que ve a todos."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def overview(self, *, q: str | None = None, limit: int = 20) -> list[AccountOverview]:
        weeks = (
            select(
                PlanCycleRow.client_id.label("cid"),
                func.count(func.distinct(PlanCycleRow.week_start)).label("weeks"),
                func.max(PlanCycleRow.week_start).label("last_week"),
            )
            .group_by(PlanCycleRow.client_id)
            .subquery()
        )
        checkins = (
            select(
                WeightEntryRow.client_id.label("cid"),
                func.max(WeightEntryRow.week_start).label("last_checkin"),
            )
            .group_by(WeightEntryRow.client_id)
            .subquery()
        )
        stmt = (
            select(
                UserRow.id,
                UserRow.tenant_id,
                UserRow.name,
                UserRow.email,
                UserRow.is_active,
                UserRow.created_at,
                ClientRow.id.label("client_id"),
                ClientRow.weight_kg,
                ClientRow.goal,
                weeks.c.weeks,
                weeks.c.last_week,
                checkins.c.last_checkin,
            )
            .outerjoin(ClientRow, ClientRow.user_id == UserRow.id)
            .outerjoin(weeks, weeks.c.cid == ClientRow.id)
            .outerjoin(checkins, checkins.c.cid == ClientRow.id)
            .where(UserRow.role == Role.USER.value, UserRow.deleted_at.is_(None))
            .order_by(UserRow.created_at.desc())
        )
        needle = (q or "").strip()
        if needle:
            like = f"%{needle.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(UserRow.email).like(like),
                    func.lower(UserRow.name).like(like),
                )
            )
        stmt = stmt.limit(limit)
        rows = (await self._s.execute(stmt)).all()
        kcal = await self._current_kcal([r.client_id for r in rows if r.client_id])
        return [
            AccountOverview(
                user_id=r.id,
                tenant_id=r.tenant_id,
                client_id=r.client_id,
                name=r.name,
                email=r.email,
                is_active=bool(r.is_active),
                created_at=r.created_at,
                weight_kg=r.weight_kg,
                goal=r.goal,
                kcal=kcal.get(r.client_id) if r.client_id else None,
                weeks_generated=int(r.weeks or 0),
                last_week=r.last_week,
                last_checkin=r.last_checkin,
            )
            for r in rows
        ]

    async def _current_kcal(self, client_ids: list[UUID]) -> dict[UUID, int]:
        """Las kcal vigentes de cada perfil: la última fila de `nutrition_targets`.

        La tabla es append-only, así que "vigente" es "la más reciente"; sin el
        `max(computed_at)` saldría cualquiera de las del historial.
        """
        if not client_ids:
            return {}
        latest = (
            select(
                NutritionTargetsRow.client_id.label("cid"),
                func.max(NutritionTargetsRow.computed_at).label("at"),
            )
            .where(NutritionTargetsRow.client_id.in_(client_ids))
            .group_by(NutritionTargetsRow.client_id)
            .subquery()
        )
        stmt = select(NutritionTargetsRow.client_id, NutritionTargetsRow.daily).join(
            latest,
            (latest.c.cid == NutritionTargetsRow.client_id)
            & (latest.c.at == NutritionTargetsRow.computed_at),
        )
        rows = (await self._s.execute(stmt)).all()
        return {
            r.client_id: int(float(r.daily.get("kcal", 0)))
            for r in rows
            if isinstance(r.daily, dict)
        }

    async def plan_weeks_for_clients(
        self, client_ids: list[UUID]
    ) -> dict[UUID, list[tuple[datetime, date]]]:
        """Cuándo se generó cada semana, para medir el saldo en lote."""
        if not client_ids:
            return {}
        stmt = select(
            PlanCycleRow.client_id,
            PlanCycleRow.created_at,
            PlanCycleRow.week_start,
        ).where(PlanCycleRow.client_id.in_(client_ids))
        rows = (await self._s.execute(stmt)).all()
        out: dict[UUID, list[tuple[datetime, date]]] = {cid: [] for cid in client_ids}
        for r in rows:
            out.setdefault(r.client_id, []).append((_aware(r.created_at), r.week_start))
        return out

    async def closed_without_next_plan(
        self, *, closed_week: date, target_week: date
    ) -> list[AutoWeekCandidate]:
        """Cerró (peso + frase) y no tiene menú del lunes objetivo."""
        ya_tiene = exists(
            select(PlanCycleRow.id).where(
                PlanCycleRow.client_id == ClientRow.id,
                PlanCycleRow.week_start == target_week,
            )
        )
        stmt = (
            select(ClientRow.id, ClientRow.tenant_id, ClientRow.user_id)
            .join(UserRow, UserRow.id == ClientRow.user_id)
            .join(
                WeightEntryRow,
                (WeightEntryRow.client_id == ClientRow.id)
                & (WeightEntryRow.week_start == closed_week),
            )
            .where(
                UserRow.role == Role.USER.value,
                UserRow.deleted_at.is_(None),
                UserRow.is_active.is_(True),
                WeightEntryRow.client_comment.is_not(None),
                func.length(func.trim(WeightEntryRow.client_comment)) >= MIN_COMMENT_CHARS,
                ~ya_tiene,
            )
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            AutoWeekCandidate(client_id=r.id, tenant_id=r.tenant_id, user_id=r.user_id)
            for r in rows
        ]
