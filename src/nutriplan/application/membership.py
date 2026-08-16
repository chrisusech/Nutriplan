"""Membresía: conceder semanas y decidir si alguien puede generar la siguiente.

Aquí vive el negocio: al registrarse regalamos una semana, el super_user activa
el mes, y generar consume saldo. Las reglas puras están en `domain/membership.py`;
esto solo las conecta con los repositorios.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

import structlog

from nutriplan.domain.membership import (
    DEFAULT_PLAN_DAYS,
    DEFAULT_PLAN_WEEKS,
    FREE_TRIAL_DAYS,
    FREE_TRIAL_WEEKS,
    IAP_PRODUCTS,
    GrantSource,
    MembershipGrant,
    MembershipState,
    can_regenerate_week,
    consumption_window,
    evaluate_membership,
    unlimited_membership,
)
from nutriplan.domain.models import Account, Role
from nutriplan.domain.week import iso_week_start

logger = structlog.get_logger(__name__)


class MembershipRepository(Protocol):
    async def add(self, grant: MembershipGrant) -> MembershipGrant: ...
    async def list_for_user(self, user_id: UUID) -> list[MembershipGrant]: ...
    async def has_source(self, user_id: UUID, source: GrantSource) -> bool: ...
    async def find_by_external_ref(self, external_ref: str) -> MembershipGrant | None: ...
    async def expire_by_original_transaction(
        self, original_transaction_id: str, *, now: datetime
    ) -> int: ...
    async def find_user_by_original_transaction(
        self, original_transaction_id: str
    ) -> UUID | None: ...


class WeeksUsedSource(Protocol):
    async def count_weeks_since(self, client_id: UUID, since: datetime) -> int: ...


class WeekGenerationCounter(Protocol):
    """Cuántas veces se pidió generar ESTA semana, contando los intentos.

    No se puede contar planes: al regenerar se borra el borrador anterior y
    siempre queda uno. Los jobs, en cambio, quedan.
    """

    async def count_generations(self, client_id: UUID, week_start: date) -> int: ...


async def grant_free_trial(
    *, account: Account, memberships: MembershipRepository, now: datetime | None = None
) -> MembershipGrant | None:
    """La semana de regalo del alta. Idempotente: solo se regala una vez."""
    if account.role is Role.SUPER_USER:
        return None
    if await memberships.has_source(account.id, GrantSource.SIGNUP_FREE):
        return None
    moment = now or datetime.now(UTC)
    grant = MembershipGrant(
        id=uuid4(),
        tenant_id=account.tenant_id,
        user_id=account.id,
        weeks=FREE_TRIAL_WEEKS,
        granted_at=moment,
        expires_at=moment + timedelta(days=FREE_TRIAL_DAYS),
        source=GrantSource.SIGNUP_FREE,
        note="Semana de prueba del alta",
    )
    logger.info("membership_trial_granted", user_id=str(account.id))
    return await memberships.add(grant)


async def grant_weeks(
    *,
    account: Account,
    memberships: MembershipRepository,
    weeks: int = DEFAULT_PLAN_WEEKS,
    days_valid: int | None = DEFAULT_PLAN_DAYS,
    granted_by: UUID | None = None,
    source: GrantSource = GrantSource.MANUAL,
    external_ref: str | None = None,
    original_transaction_id: str | None = None,
    note: str | None = None,
    now: datetime | None = None,
) -> MembershipGrant:
    """Activa el plan de alguien. Es lo que hace el super_user al cobrar.

    `source`/`external_ref` quedan abiertos porque el día que haya pasarela su
    webhook llamará exactamente a esta función con `source=payment`.
    """
    moment = now or datetime.now(UTC)
    grant = MembershipGrant(
        id=uuid4(),
        tenant_id=account.tenant_id,
        user_id=account.id,
        weeks=weeks,
        granted_at=moment,
        expires_at=(moment + timedelta(days=days_valid) if days_valid is not None else None),
        source=source,
        granted_by=granted_by,
        external_ref=external_ref,
        original_transaction_id=original_transaction_id,
        note=note,
    )
    logger.info(
        "membership_granted",
        user_id=str(account.id),
        weeks=weeks,
        source=source.value,
    )
    return await memberships.add(grant)


async def grant_iap(
    *,
    account: Account,
    memberships: MembershipRepository,
    product_id: str,
    transaction_id: str,
    original_transaction_id: str | None = None,
    now: datetime | None = None,
) -> MembershipGrant:
    """Apple (o Play) confirma el cobro: insertamos la concesión. Idempotente."""
    ref = transaction_id.strip()[:120]
    if not ref:
        raise ValueError("Falta el identificador de la transacción.")
    existing = await memberships.find_by_external_ref(ref)
    if existing is not None:
        return existing
    pair = IAP_PRODUCTS.get(product_id.strip())
    if pair is None:
        raise ValueError(f"Producto desconocido: {product_id}")
    weeks, days = pair
    original = (original_transaction_id or ref).strip()[:120] or None
    return await grant_weeks(
        account=account,
        memberships=memberships,
        weeks=weeks,
        days_valid=days,
        source=GrantSource.PAYMENT,
        external_ref=ref,
        original_transaction_id=original,
        note=product_id.strip(),
        now=now,
    )


def state_from_grants(
    grants: list[MembershipGrant],
    *,
    week_starts: list[tuple[datetime, date]],
    now: datetime,
) -> MembershipState:
    """Saldo de una cuenta sin más I/O: grants y las semanas que ya generó."""
    window = consumption_window(grants, now=now)
    used = 0
    if window is not None:
        used = len({week for created, week in week_starts if created >= window})
    return evaluate_membership(grants, weeks_used=used, now=now)


async def membership_of(
    *,
    account: Account,
    client_id: UUID | None,
    memberships: MembershipRepository,
    plans: WeeksUsedSource,
    now: datetime | None = None,
) -> MembershipState:
    """El saldo de una cuenta, listo para la puerta y para la UI."""
    if account.role is Role.SUPER_USER:
        return unlimited_membership()
    moment = now or datetime.now(UTC)
    grants = await memberships.list_for_user(account.id)
    # Lo gastado se mide en la misma ventana que lo concedido: desde que arrancó
    # la concesión viva más antigua. Si no hay ninguna viva no hay saldo, así que
    # tampoco hay nada que restarle.
    window = consumption_window(grants, now=moment)
    weeks_used = (
        await plans.count_weeks_since(client_id, window)
        if client_id is not None and window is not None
        else 0
    )
    return evaluate_membership(grants, weeks_used=weeks_used, now=moment)


async def can_generate_week(
    *,
    account: Account,
    client_id: UUID,
    memberships: MembershipRepository,
    plans: WeeksUsedSource,
    generations: WeekGenerationCounter,
    week_start: date | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """¿Puede generar el menú de esta semana? Y si no, qué decirle.

    Rehacer la semana en curso no gasta saldo: la persona pidió ESTA semana y ya
    la pagó. Solo se le pone un tope para que rehacerla no se vuelva una barra
    libre de llamadas al proveedor.
    """
    if account.role is Role.SUPER_USER:
        return True, ""

    week = week_start or iso_week_start()
    already = await generations.count_generations(client_id, week)
    if already > 0:
        if can_regenerate_week(generations_this_week=already):
            return True, ""
        return False, (
            "Ya rehiciste el menú de esta semana un par de veces. Vívela unos "
            "días y el lunes generamos la siguiente."
        )

    state = await membership_of(
        account=account,
        client_id=client_id,
        memberships=memberships,
        plans=plans,
        now=now,
    )
    return (True, "") if state.can_generate else (False, state.hint)
