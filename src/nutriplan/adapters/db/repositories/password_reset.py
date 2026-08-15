"""Enlaces de recuperación: se emiten, se consumen una vez y se tiran.

No filtra por tenant a propósito, igual que el repositorio de cuentas: quien
pide recuperar su contraseña todavía no tiene sesión, y es justamente el token
el que dice de quién es la cuenta.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import PasswordResetTokenRow
from nutriplan.domain.password_reset import PasswordResetToken


class SqlPasswordResetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, token: PasswordResetToken) -> None:
        self._s.add(
            PasswordResetTokenRow(
                id=uuid4(),
                tenant_id=token.tenant_id,
                user_id=token.user_id,
                token_hash=token.token_hash,
                expires_at=token.expires_at,
                created_at=datetime.now(UTC),
            )
        )
        await self._s.flush()

    async def peek(self, token_hash: str, *, now: datetime) -> UUID | None:
        """De quién es el enlace, sin gastarlo. Es lo que pinta la pantalla."""
        stmt = select(PasswordResetTokenRow.user_id).where(
            PasswordResetTokenRow.token_hash == token_hash,
            PasswordResetTokenRow.used_at.is_(None),
            PasswordResetTokenRow.expires_at > now,
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def consume(self, token_hash: str, *, now: datetime) -> UUID | None:
        """Marca el token como usado y devuelve de quién era. `None` si no vale.

        Marcar y comprobar en un solo UPDATE es lo que hace que dos clics a la
        vez sobre el mismo enlace no ganen los dos.
        """
        stmt = (
            update(PasswordResetTokenRow)
            .where(
                PasswordResetTokenRow.token_hash == token_hash,
                PasswordResetTokenRow.used_at.is_(None),
                PasswordResetTokenRow.expires_at > now,
            )
            .values(used_at=now)
        )
        result = cast("CursorResult[Any]", await self._s.execute(stmt))
        await self._s.flush()
        if int(result.rowcount or 0) == 0:
            return None
        row = (
            await self._s.execute(
                select(PasswordResetTokenRow).where(PasswordResetTokenRow.token_hash == token_hash)
            )
        ).scalar_one_or_none()
        return row.user_id if row is not None else None

    async def invalidate_for_user(self, user_id: UUID) -> None:
        """Cambió la contraseña (o se enlazó un proveedor): lo pendiente sobra."""
        await self._s.execute(
            delete(PasswordResetTokenRow).where(PasswordResetTokenRow.user_id == user_id)
        )
        await self._s.flush()
