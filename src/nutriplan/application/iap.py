"""Compras de App Store: verificar, conceder, renovar y caducar."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

import structlog

from nutriplan.application.membership import MembershipRepository, grant_iap
from nutriplan.domain.membership import APP_BUNDLE_ID, IAP_PRODUCTS, MembershipGrant
from nutriplan.domain.models import Account
from nutriplan.ports.iap import IAPError, StoreNotification, VerifiedPurchase

logger = structlog.get_logger(__name__)

_RENEW = frozenset({"DID_RENEW", "SUBSCRIBED"})
_EXPIRE = frozenset({"REFUND", "REVOKE", "EXPIRED", "GRACE_PERIOD_EXPIRED"})


class AccountById(Protocol):
    async def get_by_id(self, user_id: UUID) -> Account | None: ...


async def activate_verified_purchase(
    *,
    account: Account,
    memberships: MembershipRepository,
    purchase: VerifiedPurchase,
    now: datetime | None = None,
) -> MembershipGrant:
    """Concede semanas solo si el JWS ya se verificó y es de esta app."""
    if purchase.bundle_id != APP_BUNDLE_ID:
        raise IAPError("La transacción no es para esta aplicación.")
    if purchase.product_id not in IAP_PRODUCTS:
        raise IAPError("Ese producto no está a la venta.")
    logger.info(
        "iap_verified",
        environment=purchase.environment,
        product=purchase.product_id,
    )
    return await grant_iap(
        account=account,
        memberships=memberships,
        product_id=purchase.product_id,
        transaction_id=purchase.transaction_id,
        original_transaction_id=purchase.original_transaction_id,
        now=now,
    )


async def apply_store_notification(
    *,
    notification: StoreNotification,
    memberships: MembershipRepository,
    accounts: AccountById,
    now: datetime,
) -> str:
    """DID_RENEW concede; REFUND/REVOKE/EXPIRED caduca el original."""
    purchase = notification.purchase
    if purchase is None:
        raise IAPError("La notificación no trae transacción.")
    ntype = notification.notification_type.upper()
    if ntype in _RENEW:
        user_id = await memberships.find_user_by_original_transaction(
            purchase.original_transaction_id
        )
        if user_id is None:
            logger.warning("iap_renew_unknown_original", txn=purchase.transaction_id)
            return "ignored"
        account = await accounts.get_by_id(user_id)
        if account is None:
            return "ignored"
        await activate_verified_purchase(
            account=account, memberships=memberships, purchase=purchase, now=now
        )
        return "renewed"
    if ntype in _EXPIRE:
        n = await memberships.expire_by_original_transaction(
            purchase.original_transaction_id, now=now
        )
        logger.info("iap_expired", rows=n, original=purchase.original_transaction_id)
        return "expired"
    return "ignored"
