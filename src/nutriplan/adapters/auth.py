"""Hashing de contraseñas con scrypt (stdlib) — sin dependencias externas.

Formato almacenado: "scrypt$<n>$<r>$<p>$<salt_hex>$<hash_hex>". scrypt es
memoria-duro (resistente a fuerza bruta con GPU); parámetros conservadores para
Nivel 1. La verificación es en tiempo constante.
"""

import hashlib
import hmac
import os

_N, _R, _P, _DKLEN = 2**14, 8, 1, 32


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2,
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, TypeError):
        return False
