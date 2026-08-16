"""Verificación de los ID token que devuelven Google y Apple.

En móvil el token lo produce el plugin nativo (Google bloquea su login dentro de
un WebView plano) y llega aquí para canjearse por una sesión. Verificar es
obligatorio: sin ello, cualquiera manda un JSON con el correo ajeno.

Se usa el endpoint `tokeninfo` de cada proveedor en vez de validar la firma con
JWKS a mano. Es una llamada de red por login —despreciable frente a generar un
menú— y evita mantener criptografía propia, que es donde se cometen los errores.
"""

from dataclasses import dataclass

import httpx
import structlog

from nutriplan.domain.errors import NutriPlanError
from nutriplan.domain.models import AuthProvider

logger = structlog.get_logger(__name__)

GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
APPLE_KEYS = "https://appleid.apple.com/auth/keys"
TIMEOUT_S = 10.0


class OAuthError(NutriPlanError):
    """El token no se pudo verificar, o no es para esta app."""


@dataclass(frozen=True)
class VerifiedIdentity:
    provider: AuthProvider
    subject: str
    email: str
    name: str
    email_verified: bool


def _truthy(value: object) -> bool:
    return str(value).lower() in ("true", "1")


async def verify_google_id_token(id_token: str, *, client_id: str) -> VerifiedIdentity:
    """Comprueba firma, caducidad y destinatario contra Google."""
    if not client_id:
        raise OAuthError("Falta GOOGLE_CLIENT_ID en la configuración")
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as http:
            # POST: el JWT no viaja en la query (httpx a INFO la imprimiría).
            response = await http.post(GOOGLE_TOKENINFO, data={"id_token": id_token})
    except httpx.HTTPError as exc:
        raise OAuthError("No se pudo verificar el token con Google") from exc
    if response.status_code != 200:
        raise OAuthError("El token de Google no es válido")

    claims = response.json()
    # `aud` es lo que impide que sirva un token emitido para OTRA app.
    if claims.get("aud") != client_id:
        raise OAuthError("El token de Google no es para esta aplicación")
    subject = str(claims.get("sub") or "")
    email = str(claims.get("email") or "")
    if not subject or not email:
        raise OAuthError("El token de Google no trae identidad utilizable")
    return VerifiedIdentity(
        provider=AuthProvider.GOOGLE,
        subject=subject,
        email=email,
        name=str(claims.get("name") or ""),
        email_verified=_truthy(claims.get("email_verified")),
    )


async def verify_apple_id_token(id_token: str, *, client_id: str) -> VerifiedIdentity:
    """Verifica un ID token de Apple contra su JWKS.

    Apple no publica un `tokeninfo`, así que aquí sí hay que validar la firma.
    """
    if not client_id:
        raise OAuthError("Falta APPLE_CLIENT_ID en la configuración")
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as exc:  # pragma: no cover - depende del extra `apple`
        raise OAuthError(
            "Falta la dependencia de Apple: instala el extra `apple` (pyjwt[crypto])"
        ) from exc

    try:
        signing_key = PyJWKClient(APPLE_KEYS).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=client_id,
            issuer="https://appleid.apple.com",
        )
    except Exception as exc:
        raise OAuthError("El token de Apple no es válido") from exc

    subject = str(claims.get("sub") or "")
    email = str(claims.get("email") or "")
    if not subject:
        raise OAuthError("El token de Apple no trae identidad utilizable")
    return VerifiedIdentity(
        provider=AuthProvider.APPLE,
        subject=subject,
        email=email,
        # Apple manda el nombre UNA sola vez, fuera del token, en el primer alta.
        name="",
        email_verified=_truthy(claims.get("email_verified")),
    )
