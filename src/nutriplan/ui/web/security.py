"""Las cuatro piezas que hacen la app segura de exponer.

Cada una es independiente y se prueba sola: cabeceras, CSRF, límite de intentos
y el guard de sesión. Un solo middleware con todo dentro sería imposible de
razonar y de testear por partes.
"""

import secrets
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

logger = structlog.get_logger(__name__)

Handler = Callable[[Request], Awaitable[Response]]

CSRF_FIELD = "_csrf"  # nombre del campo oculto en los formularios
CSRF_HEADER = "X-CSRF-Token"  # HTMX lo manda aquí
SESSION_KEY = "csrf"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# Rutas que un tercero SÍ puede invocar: el ID token de Google llega desde el
# cliente nativo, que no tiene cookie de sesión ni de dónde sacar el token CSRF.
CSRF_EXEMPT = ("/auth/oauth/",)


def csrf_token(request: Request) -> str:
    """El token de esta sesión; se crea la primera vez que se pide."""
    if "session" not in request.scope:
        return ""
    token = request.session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_KEY] = token
    return str(token)


async def _submitted_token(request: Request) -> str:
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    content_type = request.headers.get("content-type", "")
    if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
        form = await request.form()
        return str(form.get(CSRF_FIELD) or "")
    return ""


async def csrf_middleware(request: Request, call_next: Handler) -> Response:
    """Rechaza los POST que no traen el token de la sesión.

    `SameSite=Lax` ya frena el POST entre sitios, pero es una sola línea de
    defensa y depende del navegador. Esto es la segunda.
    """
    if request.method in SAFE_METHODS or request.url.path.startswith(CSRF_EXEMPT):
        return await call_next(request)

    expected = request.session.get(SESSION_KEY) if "session" in request.scope else None
    submitted = await _submitted_token(request)
    if not expected or not submitted or not secrets.compare_digest(str(expected), submitted):
        logger.warning("csrf_rejected", path=request.url.path)
        return PlainTextResponse(
            "Tu sesión caducó. Recarga la página e inténtalo de nuevo.", status_code=403
        )
    return await call_next(request)


Middleware = Callable[[Request, Handler], Awaitable[Response]]


def security_headers_middleware(*, https: bool) -> Middleware:
    """Las cabeceras que un navegador necesita para defenderte.

    La CSP no lleva `unsafe-inline`: por eso el color de marca viaja en un
    atributo del `<body>` y no en un `<style>`, y el JavaScript vive en
    `app.js`. Relajarla aquí desharía ese trabajo.
    """
    csp = "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ])

    async def middleware(request: Request, call_next: Handler) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        response.headers["X-Frame-Options"] = "DENY"
        if https:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )
        return response

    return middleware


class RateLimiter:
    """Ventana deslizante por IP y ruta, en memoria.

    En proceso a propósito: con un worker basta, y con varios el peor caso es
    que el tope efectivo se multiplique por el número de workers — sigue siendo
    mejor que no tener ninguno. Redis cuando haga falta de verdad.
    """

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], list[float]] = defaultdict(list)

    def check(self, key: str, ip: str, *, limit: int, window_s: float) -> bool:
        now = time.monotonic()
        hits = self._hits[(key, ip)]
        hits[:] = [t for t in hits if now - t < window_s]
        if len(hits) >= limit:
            return False
        hits.append(now)
        return True


# Cuánto se tolera por IP. El login es el caro: adivinar contraseñas contra un
# scrypt de 16 MB es además una forma de agotar la CPU del servidor.
RATE_LIMITS: dict[str, tuple[int, float]] = {
    "/login": (10, 300),
    "/registro": (5, 3600),
    "/recuperar": (5, 3600),
    "/generar": (20, 3600),
    "/feedback": (20, 3600),
}


def _client_ip(request: Request) -> str:
    # Detrás de un proxy, el cliente real va en X-Forwarded-For. Se usa el
    # primero, que es el único que el proxy no deja falsificar.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "desconocido"


def rate_limit_middleware(limiter: RateLimiter) -> Middleware:
    async def middleware(request: Request, call_next: Handler) -> Response:
        if request.method in SAFE_METHODS:
            return await call_next(request)
        path = request.url.path
        rule = next(((p, r) for p, r in RATE_LIMITS.items() if path.startswith(p)), None)
        if rule is not None:
            key, (limit, window) = rule[0], rule[1]
            if not limiter.check(key, _client_ip(request), limit=limit, window_s=window):
                logger.warning("rate_limited", path=path)
                return JSONResponse(
                    {"detail": "Demasiados intentos. Espera un momento."}, status_code=429
                )
        return await call_next(request)

    return middleware
