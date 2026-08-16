"""Verificación de compras de la tienda. Apple firma; nosotros no inventamos ids."""

from __future__ import annotations

from dataclasses import dataclass

from nutriplan.domain.errors import NutriPlanError


class IAPError(NutriPlanError):
    """El JWS no se pudo verificar, o no es para esta app."""


@dataclass(frozen=True)
class VerifiedPurchase:
    transaction_id: str
    original_transaction_id: str
    product_id: str
    bundle_id: str
    environment: str  # Production | Sandbox


@dataclass(frozen=True)
class StoreNotification:
    notification_type: str
    purchase: VerifiedPurchase | None


class StoreTransactionVerifier:
    """Puerto: un JWS de Apple entra, una compra verificada sale."""

    def verify_transaction(self, signed_transaction: str) -> VerifiedPurchase:
        raise NotImplementedError

    def verify_notification(self, signed_payload: str) -> StoreNotification:
        raise NotImplementedError
