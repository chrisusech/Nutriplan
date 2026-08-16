"""El adaptador comprueba la firma ES256; un JWS inventado no pasa."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from nutriplan.adapters.iap.apple_jws import AppleJWSVerifier
from nutriplan.domain.membership import APP_BUNDLE_ID, IAP_MONTHLY_PRODUCT
from nutriplan.ports.iap import IAPError


def _signed_jws(payload: dict[str, object]) -> str:
    import base64

    import jwt

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-apple")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    der = cert.public_bytes(serialization.Encoding.DER)
    x5c = [base64.b64encode(der).decode("ascii")]
    token = jwt.encode(payload, key, algorithm="ES256", headers={"x5c": x5c})
    return token if isinstance(token, str) else token.decode("ascii")


def _txn_payload(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "transactionId": "2001",
        "originalTransactionId": "2000",
        "productId": IAP_MONTHLY_PRODUCT,
        "bundleId": APP_BUNDLE_ID,
        "environment": "Sandbox",
    }
    body.update(overrides)
    return body


def test_un_jws_de_sandbox_firmado_se_acepta() -> None:
    token = _signed_jws(_txn_payload())
    purchase = AppleJWSVerifier().verify_transaction(token)
    assert purchase.transaction_id == "2001"
    assert purchase.original_transaction_id == "2000"
    assert purchase.environment == "Sandbox"
    assert purchase.bundle_id == APP_BUNDLE_ID


def test_un_jws_de_otro_bundle_se_rechaza() -> None:
    token = _signed_jws(_txn_payload(bundleId="com.otra.app"))
    with pytest.raises(IAPError, match="esta aplicación"):
        AppleJWSVerifier().verify_transaction(token)


def test_un_jws_sin_firma_no_concede() -> None:
    with pytest.raises(IAPError):
        AppleJWSVerifier().verify_transaction("aaa.bbb.ccc")


def test_una_notificacion_v2_trae_la_transaccion_anidada() -> None:
    inner = _signed_jws(_txn_payload(transactionId="3002"))
    outer = _signed_jws(
        {
            "notificationType": "DID_RENEW",
            "data": {"signedTransactionInfo": inner},
        }
    )
    note = AppleJWSVerifier().verify_notification(outer)
    assert note.notification_type == "DID_RENEW"
    assert note.purchase is not None
    assert note.purchase.transaction_id == "3002"


def test_uuid_no_se_usa_como_atajo() -> None:
    """El id de la cuenta no es un JWS: el verificador lo tira."""
    with pytest.raises(IAPError):
        AppleJWSVerifier().verify_transaction(str(uuid4()))
