"""Enlaces firmados para confirmar un correo.

Sin estado a propósito: confirmar el correo es idempotente y no abre nada —
volver a abrir el enlace solo vuelve a marcar verificado lo que ya lo estaba.

Recuperar la contraseña NO usa esto, y no debe volver a usarlo: ese enlace sí
abre la cuenta, así que tiene que poder retirarse en cuanto se usa. Vive en
`domain/password_reset.py`, con su fila detrás.
"""

import base64
import hashlib
import hmac
import time


def issue_token(*, email: str, secret: str, ttl_seconds: int = 3600) -> str:
    exp = int(time.time()) + ttl_seconds
    payload = f"{email.strip().lower()}|{exp}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    raw = f"{payload}|{sig}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def verify_token(token: str, *, secret: str) -> str | None:
    """Devuelve el correo si el token es válido y no expiró."""
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        email, exp_s, sig = raw.split("|", 2)
        payload = f"{email}|{exp_s}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        if int(exp_s) < int(time.time()):
            return None
        return email
    except (ValueError, TypeError):
        return None
