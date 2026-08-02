"""Perfiles nutricionales y sus preferencias."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    ClientFoodBanRow,
    ClientFoodPreferenceRow,
    ClientRow,
    UserRow,
)
from nutriplan.domain.errors import TenantIsolationError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    MealSlot,
    Sex,
)


def _aware(dt: datetime) -> datetime:
    """SQLite devuelve datetimes naive; se asumen UTC para round-trips estables."""
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


class SqlClientRepository:
    def __init__(self, session: AsyncSession, tenant_id: UUID) -> None:
        self._s = session
        self._tenant = tenant_id

    def _to_row(self, c: Client) -> ClientRow:
        return ClientRow(
            id=c.id,
            tenant_id=self._tenant,
            user_id=c.user_id,
            sex=c.sex.value,
            age_years=c.age_years,
            height_cm=c.height_cm,
            weight_kg=c.weight_kg,
            goal=c.goal.value,
            activity_level=c.activity_level.value,
            city=c.city,
            country=c.country,
            restrictions=list(c.restrictions),
            dislikes=list(c.dislikes),
            context_tags=list(c.context_tags),
            eating_pattern_raw=c.eating_pattern_raw,
            meal_slots=[s.value for s in c.meal_slots],
            free_meal_day=c.free_meal_day,
            free_meal_slot=c.free_meal_slot.value if c.free_meal_slot else None,
            active_plan_id=c.active_plan_id,
            preferences=[
                ClientFoodPreferenceRow(tenant_id=self._tenant, client_id=c.id, food_id=fid)
                for fid in c.liked_food_ids
            ],
        )

    @staticmethod
    def _to_domain(row: ClientRow) -> Client:
        return Client(
            id=row.id,
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            # El nombre vive en la cuenta, no en el perfil: una sola fuente.
            name=row.account.name,
            sex=Sex(row.sex),
            age_years=row.age_years,
            height_cm=row.height_cm,
            weight_kg=row.weight_kg,
            goal=Goal(row.goal),
            activity_level=ActivityLevel(row.activity_level),
            city=row.city,
            country=row.country,
            liked_food_ids=[p.food_id for p in row.preferences],
            restrictions=list(row.restrictions or []),
            dislikes=list(row.dislikes or []),
            context_tags=list(row.context_tags or []),
            eating_pattern_raw=row.eating_pattern_raw,
            meal_slots=[MealSlot(s) for s in (row.meal_slots or [])],
            free_meal_day=row.free_meal_day,
            free_meal_slot=(
                MealSlot(row.free_meal_slot) if row.free_meal_slot else None
            ),
            active_plan_id=row.active_plan_id,
        )

    async def add(self, client: Client) -> None:
        if client.tenant_id != self._tenant:
            raise TenantIsolationError("El perfil no pertenece al tenant del contexto")
        self._s.add(self._to_row(client))
        await self._s.flush()

    async def get(self, client_id: UUID) -> Client | None:
        stmt = select(ClientRow).where(
            ClientRow.id == client_id, ClientRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def get_by_user(self, user_id: UUID) -> Client | None:
        """El perfil de una cuenta. Uno como mucho: la columna es única."""
        stmt = select(ClientRow).where(
            ClientRow.user_id == user_id, ClientRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        return self._to_domain(row) if row else None

    async def list_all(self) -> list[Client]:
        stmt = (
            select(ClientRow)
            .join(UserRow, UserRow.id == ClientRow.user_id)
            .where(ClientRow.tenant_id == self._tenant)
            .order_by(UserRow.name)
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [self._to_domain(r) for r in rows]

    async def update(self, client: Client) -> None:
        stmt = select(ClientRow).where(
            ClientRow.id == client.id, ClientRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise TenantIsolationError("Cliente inexistente para este tenant")
        # Inamovibles tras el alta: sexo y altura. Cambiarlos rompería la identidad
        # del perfil y el Mifflin-St Jeor sobre el que se compararon las versiones.
        # El peso SÍ cambia: es el seguimiento.
        row.age_years = client.age_years
        row.weight_kg = client.weight_kg
        row.goal = client.goal.value
        row.activity_level = client.activity_level.value
        row.city = client.city
        row.country = client.country
        row.restrictions = list(client.restrictions)
        row.dislikes = list(client.dislikes)
        row.context_tags = list(client.context_tags)
        row.meal_slots = [s.value for s in client.meal_slots]
        row.free_meal_day = client.free_meal_day
        row.free_meal_slot = (
            client.free_meal_slot.value if client.free_meal_slot else None
        )
        # borrar preferencias viejas antes de insertar (unique client_id+food_id)
        row.preferences.clear()
        await self._s.flush()
        row.preferences = [
            ClientFoodPreferenceRow(tenant_id=self._tenant, client_id=client.id, food_id=fid)
            for fid in client.liked_food_ids
        ]
        await self._s.flush()

    async def set_active_plan(self, client_id: UUID, plan_id: UUID | None) -> None:
        """Cuál de sus planes es EL plan. Activar uno archiva al anterior.

        No pasa por `update()` a propósito: aquello reescribe las preferencias del
        cliente enteras, y activar un plan no tiene por qué tocar lo que le gusta
        comer.
        """
        stmt = select(ClientRow).where(
            ClientRow.id == client_id, ClientRow.tenant_id == self._tenant
        )
        row = (await self._s.execute(stmt)).scalar_one_or_none()
        if row is None:
            raise TenantIsolationError("Cliente inexistente para este tenant")
        row.active_plan_id = plan_id
        await self._s.flush()

    async def list_banned_food_ids(self, client_id: UUID) -> list[UUID]:
        stmt = select(ClientFoodBanRow.food_id).where(
            ClientFoodBanRow.client_id == client_id,
            ClientFoodBanRow.tenant_id == self._tenant,
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def ban_food(self, client_id: UUID, food_id: UUID) -> None:
        stmt = select(ClientFoodBanRow).where(
            ClientFoodBanRow.client_id == client_id,
            ClientFoodBanRow.food_id == food_id,
            ClientFoodBanRow.tenant_id == self._tenant,
        )
        if (await self._s.execute(stmt)).scalar_one_or_none() is not None:
            return
        self._s.add(
            ClientFoodBanRow(
                tenant_id=self._tenant, client_id=client_id, food_id=food_id
            )
        )
        await self._s.flush()
