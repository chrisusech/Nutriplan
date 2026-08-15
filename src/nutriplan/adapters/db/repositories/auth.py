"""Cuentas: alta, acceso y borrado."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AppEventRow,
    AppFeedbackRow,
    ClientFoodBanRow,
    ClientFoodPreferenceRow,
    ClientRow,
    ClientTasteSignalRow,
    DayPlanRow,
    DeviceTokenRow,
    DishRatingRow,
    GenerationJobRow,
    MealEntryRow,
    MealItemRow,
    MembershipGrantRow,
    NutritionTargetsRow,
    PlanCycleRow,
    TenantRow,
    UserRow,
    WeightEntryRow,
)
from nutriplan.domain.models import (
    Account,
    AuthProvider,
    Role,
)


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes naive; se asumen UTC para round-trips estables."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class SqlAuthRepository:
    """Cuentas. NO filtra por tenant: el login es previo a la sesión y es
    justamente lo que resuelve a qué tenant pertenece quien entra."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    @staticmethod
    def _to_domain(row: UserRow) -> Account:
        return Account(
            id=row.id,
            tenant_id=row.tenant_id,
            name=row.name,
            email=row.email or "",
            role=Role(row.role),
            is_active=row.is_active,
            provider=AuthProvider(row.provider),
            email_verified_at=row.email_verified_at,
            consent_analytics_at=row.consent_analytics_at,
        )

    async def get_by_email(self, email: str) -> tuple[Account, str] | None:
        """Devuelve (cuenta, password_hash) o None. El hash no sale del adaptador."""
        stmt = select(UserRow).where(UserRow.email == email.strip().lower())
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None or row.password_hash is None:
            return None
        return self._to_domain(row), row.password_hash

    async def get_by_email_any_provider(self, email: str) -> Account | None:
        """Detecta que el correo ya existe, entrara como entrara."""
        stmt = select(UserRow).where(UserRow.email == email.strip().lower())
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_provider(self, provider: AuthProvider, subject: str) -> Account | None:
        """La cuenta que ya entró antes con este mismo Google/Apple."""
        stmt = select(UserRow).where(
            UserRow.provider == provider.value, UserRow.provider_subject == subject
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def create_account(
        self,
        *,
        tenant_id: UUID,
        tenant_name: str,
        name: str,
        email: str,
        password_hash: str | None = None,
        role: str = "user",
        provider: AuthProvider = AuthProvider.PASSWORD,
        provider_subject: str | None = None,
        email_verified_at: datetime | None = None,
    ) -> Account:
        """Crea la cuenta y su tenant: cada persona es dueña del suyo."""
        now = datetime.now(UTC)
        if await self._s.get(TenantRow, tenant_id) is None:
            self._s.add(TenantRow(id=tenant_id, name=tenant_name, created_at=now))
        user = UserRow(
            id=uuid4(),
            tenant_id=tenant_id,
            name=name,
            email=email.strip().lower(),
            password_hash=password_hash,
            role=role,
            provider=provider.value,
            provider_subject=provider_subject,
            email_verified_at=email_verified_at,
            created_at=now,
        )
        self._s.add(user)
        await self._s.flush()
        return self._to_domain(user)

    async def set_password_hash(self, email: str, password_hash: str) -> bool:
        stmt = select(UserRow).where(UserRow.email == email.strip().lower())
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            return False
        row.password_hash = password_hash
        await self._s.flush()
        return True

    async def clear_password_hash(self, user_id: UUID) -> None:
        """Deja la cuenta sin contraseña: se entra por el proveedor.

        Se usa al enlazar Google/Apple sobre una cuenta que nació con correo. Sin
        esto, quien hubiera registrado ese correo antes que su dueño se quedaba
        dentro para siempre.
        """
        row = await self._s.get(UserRow, user_id)
        if row is not None:
            row.password_hash = None
            await self._s.flush()

    async def mark_email_verified(self, user_id: UUID) -> None:
        row = await self._s.get(UserRow, user_id)
        if row is not None:
            row.email_verified_at = datetime.now(UTC)
            await self._s.flush()

    async def set_name(self, user_id: UUID, name: str) -> None:
        """El onboarding puede corregir el nombre que trajo el proveedor."""
        row = await self._s.get(UserRow, user_id)
        if row is not None and name.strip():
            row.name = name.strip()[:200]
            await self._s.flush()

    async def grant_analytics_consent(self, user_id: UUID) -> None:
        """La fecha es el consentimiento: sin ella no se registra nada suyo."""
        row = await self._s.get(UserRow, user_id)
        if row is not None and row.consent_analytics_at is None:
            row.consent_analytics_at = datetime.now(UTC)
            await self._s.flush()

    async def touch_login(self, user_id: UUID) -> None:
        row = await self._s.get(UserRow, user_id)
        if row is not None:
            row.last_login_at = datetime.now(UTC)
            await self._s.flush()

    async def link_oauth(self, user_id: UUID, provider: AuthProvider, subject: str) -> None:
        """Pega Google/Apple a una cuenta que nació con correo, sin perder el hash."""
        row = await self._s.get(UserRow, user_id)
        if row is None:
            return
        row.provider = provider.value
        row.provider_subject = subject
        await self._s.flush()

    # --- Administración (super_user): cross-tenant, no filtra por tenant. ---

    async def list_accounts(self) -> list[Account]:
        """Las cuentas de usuario final, para el panel del super_user."""
        stmt = (
            select(UserRow)
            .where(UserRow.role == Role.USER, UserRow.deleted_at.is_(None))
            .order_by(UserRow.created_at.desc())
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def get_by_id(self, user_id: UUID) -> Account | None:
        row = await self._s.get(UserRow, user_id)
        return self._to_domain(row) if row is not None else None

    async def set_active(self, user_id: UUID, is_active: bool) -> bool:
        row = await self._s.get(UserRow, user_id)
        if row is None:
            return False
        row.is_active = is_active
        await self._s.flush()
        return True


class SqlAccountEraser:
    """Borra los datos de una cuenta. Sin filtro de tenant: recibe el suyo."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def purge_tenant(self, tenant_id: UUID) -> None:
        """Todo lo que cuelga del tenant, de las hojas hacia la raíz."""
        plan_ids = (
            select(PlanCycleRow.id).where(PlanCycleRow.tenant_id == tenant_id).scalar_subquery()
        )
        day_ids = select(DayPlanRow.id).where(DayPlanRow.tenant_id == tenant_id).scalar_subquery()
        meal_ids = (
            select(MealEntryRow.id).where(MealEntryRow.tenant_id == tenant_id).scalar_subquery()
        )
        await self._s.execute(delete(MealItemRow).where(MealItemRow.meal_entry_id.in_(meal_ids)))
        await self._s.execute(delete(MealEntryRow).where(MealEntryRow.day_plan_id.in_(day_ids)))
        await self._s.execute(delete(DayPlanRow).where(DayPlanRow.plan_cycle_id.in_(plan_ids)))
        for table in (
            DeviceTokenRow,
            DishRatingRow,
            AppFeedbackRow,
            PlanCycleRow,
            NutritionTargetsRow,
            WeightEntryRow,
            ClientFoodPreferenceRow,
            ClientFoodBanRow,
            ClientTasteSignalRow,
            ClientRow,
            GenerationJobRow,
            MembershipGrantRow,
        ):
            await self._s.execute(delete(table).where(table.tenant_id == tenant_id))
        await self._s.flush()

    async def anonymize_events(self, user_id: UUID) -> None:
        """El embudo agregado sobrevive; deja de ser de nadie."""
        await self._s.execute(
            update(AppEventRow)
            .where(AppEventRow.user_id == user_id)
            .values(user_id=None, tenant_id=None, session_id=None)
        )
        await self._s.flush()

    async def mark_deleted(self, user_id: UUID) -> None:
        """La fila se conserva vacía para que el correo no se reutilice a ciegas."""
        row = await self._s.get(UserRow, user_id)
        if row is None:
            return
        row.deleted_at = datetime.now(UTC)
        row.is_active = False
        row.password_hash = None
        row.provider_subject = None
        row.name = "Cuenta eliminada"
        row.email = f"borrada+{user_id}@nutriplan.invalid"
        await self._s.flush()
