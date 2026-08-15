"""Lo que el guard de sesión acaba de comprobar, por unos segundos.

El guard corre en TODA petición privada, incluido el polling de la pantalla de
generación (~900 ms) y una petición por comida en la de la semana. Cada una
abría su propia sesión de base de datos para dos consultas, además de la que
abre la vista: el doble de conexiones por request para preguntar algo que casi
nunca cambia.

La ventana es corta a propósito. Desactivar una cuenta tiene que surtir efecto
ya, no en catorce días cuando le caduque la cookie — y quien la desactiva
además tira la entrada a mano.
"""

from __future__ import annotations

import time

from nutriplan.domain.models import Account

# Suficiente para absorber una ráfaga de polling; demasiado poco para que a
# nadie le dé tiempo a notar que su cuenta siguió abierta un momento más.
TTL_S = 30.0


class AccountGateCache:
    def __init__(self, ttl_s: float = TTL_S) -> None:
        self._ttl_s = ttl_s
        self._entries: dict[str, tuple[float, Account]] = {}

    def get(self, email: str) -> Account | None:
        entry = self._entries.get(email)
        if entry is None:
            return None
        expires_at, account = entry
        if expires_at <= time.monotonic():
            del self._entries[email]
            return None
        return account

    def put(self, email: str, account: Account) -> None:
        self._entries[email] = (time.monotonic() + self._ttl_s, account)

    def drop(self, email: str) -> None:
        """Lo que acaba de cambiar no puede seguir sirviéndose de la caché."""
        self._entries.pop(email, None)

    def clear(self) -> None:
        self._entries.clear()
