"""Tokens de push nativo (APNs / FCM)."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import DeviceTokenRow

ALLOWED_PLATFORMS = frozenset({"ios", "android"})


class SqlDeviceTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert(
        self, *, tenant_id: UUID, user_id: UUID, platform: str, token: str
    ) -> None:
        platform = platform.strip().lower()
        token = token.strip()
        if platform not in ALLOWED_PLATFORMS or not token or len(token) > 512:
            raise ValueError("plataforma o token inválidos")

        now = datetime.now(UTC)
        existing = (
            await self._session.execute(
                select(DeviceTokenRow).where(
                    DeviceTokenRow.user_id == user_id,
                    DeviceTokenRow.token == token,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.platform = platform
            existing.updated_at = now
            return
        self._session.add(
            DeviceTokenRow(
                id=uuid4(),
                tenant_id=tenant_id,
                user_id=user_id,
                platform=platform,
                token=token,
                created_at=now,
                updated_at=now,
            )
        )

    async def list_for_user(self, user_id: UUID) -> list[DeviceTokenRow]:
        rows = (
            await self._session.execute(
                select(DeviceTokenRow).where(DeviceTokenRow.user_id == user_id)
            )
        ).scalars()
        return list(rows)
