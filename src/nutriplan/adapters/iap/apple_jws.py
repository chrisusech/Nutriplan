"""Adaptador de JWS de App Store (StoreKit 2).

La firma la comprueba PyJWT contra el certificado `x5c` de la cabecera. El
payload nunca se fía sin esa firma. Sandbox y Production valen: el revisor
cobra en Sandbox contra el binario de prod.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from nutriplan.domain.membership import APP_BUNDLE_ID
from nutriplan.ports.iap import (
    IAPError,
    StoreNotification,
    StoreTransactionVerifier,
    VerifiedPurchase,
)

_OK_ENV = frozenset({"Production", "Sandbox"})


def _b64url(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def _header_and_payload(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    parts = token.split(".")
    if len(parts) != 3:
        raise IAPError("La transacción no es un JWS válido.")
    try:
        header = json.loads(_b64url(parts[0]))
        payload = json.loads(_b64url(parts[1]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise IAPError("La transacción no se pudo leer.") from exc
    if not isinstance(header, dict) or not isinstance(payload, dict):
        raise IAPError("La transacción no se pudo leer.")
    return header, payload


def _verify_signature(token: str, header: dict[str, Any]) -> None:
    chain = header.get("x5c")
    if not isinstance(chain, list) or not chain:
        raise IAPError("La transacción no trae certificado.")
    try:
        import jwt
        from cryptography.hazmat.primitives import serialization
        from cryptography.x509 import load_der_x509_certificate
    except ImportError as exc:
        raise IAPError("Falta la dependencia para verificar compras de Apple.") from exc
    try:
        cert = load_der_x509_certificate(base64.b64decode(str(chain[0])))
        key = cert.public_key()
        pem = key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        jwt.decode(
            token,
            pem,
            algorithms=["ES256"],
            options={"verify_aud": False, "verify_exp": False},
        )
    except Exception as exc:
        raise IAPError("La transacción no es válida.") from exc


def _purchase_from(payload: dict[str, Any]) -> VerifiedPurchase:
    txn = str(payload.get("transactionId") or "")
    original = str(payload.get("originalTransactionId") or txn)
    product = str(payload.get("productId") or "")
    bundle = str(payload.get("bundleId") or "")
    env = str(payload.get("environment") or "")
    if not txn or not product:
        raise IAPError("La transacción no trae identificador utilizable.")
    if env not in _OK_ENV:
        raise IAPError("El entorno de la transacción no es válido.")
    return VerifiedPurchase(
        transaction_id=txn[:120],
        original_transaction_id=original[:120],
        product_id=product,
        bundle_id=bundle,
        environment=env,
    )


class AppleJWSVerifier(StoreTransactionVerifier):
    """Verifica JWS de transacción y notificaciones V2."""

    def __init__(self, *, bundle_id: str = APP_BUNDLE_ID) -> None:
        self._bundle = bundle_id

    def verify_transaction(self, signed_transaction: str) -> VerifiedPurchase:
        token = signed_transaction.strip()
        header, payload = _header_and_payload(token)
        _verify_signature(token, header)
        purchase = _purchase_from(payload)
        if purchase.bundle_id != self._bundle:
            raise IAPError("La transacción no es para esta aplicación.")
        return purchase

    def verify_notification(self, signed_payload: str) -> StoreNotification:
        token = signed_payload.strip()
        header, payload = _header_and_payload(token)
        _verify_signature(token, header)
        ntype = str(payload.get("notificationType") or "")
        raw_data = payload.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        signed_txn = str(data.get("signedTransactionInfo") or "")
        purchase = None
        if signed_txn:
            purchase = self.verify_transaction(signed_txn)
        return StoreNotification(notification_type=ntype, purchase=purchase)
