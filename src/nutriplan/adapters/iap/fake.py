"""Verificador de pruebas: nunca habla con Apple."""

from __future__ import annotations

from nutriplan.ports.iap import (
    IAPError,
    StoreNotification,
    StoreTransactionVerifier,
    VerifiedPurchase,
)


class FakeStoreVerifier(StoreTransactionVerifier):
    """Respuestas grabadas. Un JWS inventado aquí no abre semanas en prod."""

    def __init__(
        self,
        *,
        purchases: dict[str, VerifiedPurchase] | None = None,
        notifications: dict[str, StoreNotification] | None = None,
    ) -> None:
        self.purchases = purchases or {}
        self.notifications = notifications or {}

    def verify_transaction(self, signed_transaction: str) -> VerifiedPurchase:
        found = self.purchases.get(signed_transaction.strip())
        if found is None:
            raise IAPError("La transacción no es válida.")
        return found

    def verify_notification(self, signed_payload: str) -> StoreNotification:
        found = self.notifications.get(signed_payload.strip())
        if found is None:
            raise IAPError("La notificación no es válida.")
        return found
