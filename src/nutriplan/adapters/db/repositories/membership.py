"""Concesiones de semanas. Se insertan; no se editan ni se borran."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import MembershipGrantRow
from nutriplan.adapters.db.repositories._shared import _aware
from nutriplan.domain.membership import GrantSource, MembershipGrant


class SqlMembershipRepository:
    """Sin filtro de tenant: el super_user concede semanas a cuentas ajenas y el
    saldo se pide siempre por `user_id`, que ya es la llave del dueño."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _to_domain(row: MembershipGrantRow) -> MembershipGrant:
        return MembershipGrant(
            id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            weeks=row.weeks,
            granted_at=_aware(row.granted_at),
            expires_at=_aware(row.expires_at) if row.expires_at else None,
            source=GrantSource(row.source),
            granted_by=row.granted_by,
            external_ref=row.external_ref,
            note=row.note,
        )

    async def add(self, grant: MembershipGrant) -> MembershipGrant:
        self._s.add(
            MembershipGrantRow(
                id=grant.id,
                tenant_id=grant.tenant_id,
                user_id=grant.user_id,
                weeks=grant.weeks,
                granted_at=grant.granted_at,
                expires_at=grant.expires_at,
                source=grant.source.value,
                granted_by=grant.granted_by,
                external_ref=grant.external_ref,
                note=grant.note,
            )
        )
        await self._s.flush()
        return grant

    async def list_for_user(self, user_id: UUID) -> list[MembershipGrant]:
        by_user = await self.list_for_users([user_id])
        return by_user.get(user_id, [])

    async def list_for_users(self, user_ids: list[UUID]) -> dict[UUID, list[MembershipGrant]]:
        """Todas las concesiones de un lote. Una consulta, no una por cuenta."""
        if not user_ids:
            return {}
        stmt = (
            select(MembershipGrantRow)
            .where(MembershipGrantRow.user_id.in_(user_ids))
            .order_by(MembershipGrantRow.granted_at.desc())
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        out: dict[UUID, list[MembershipGrant]] = {uid: [] for uid in user_ids}
        for row in rows:
            out.setdefault(row.user_id, []).append(self._to_domain(row))
        return out

    async def has_source(self, user_id: UUID, source: GrantSource) -> bool:
        """Para no regalar dos veces la semana de prueba."""
        stmt = select(MembershipGrantRow.id).where(
            MembershipGrantRow.user_id == user_id,
            MembershipGrantRow.source == source.value,
        )
        return (await self._s.execute(stmt)).first() is not None

    async def find_by_external_ref(self, external_ref: str) -> MembershipGrant | None:
        """Una transacción de tienda no puede conceder dos veces."""
        stmt = select(MembershipGrantRow).where(
            MembershipGrantRow.external_ref == external_ref.strip()[:120]
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None
