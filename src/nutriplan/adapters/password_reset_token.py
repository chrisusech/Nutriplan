"""Tokens firmados para restablecer contraseña (solo desarrollo local).

Sin tabla ni correo: el enlace se muestra en pantalla tras pedir recuperación.
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
