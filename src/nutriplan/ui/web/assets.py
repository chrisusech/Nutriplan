"""Cómo se sirven los estáticos, y sobre todo cuánto se pueden guardar.

Solo `app.css` y los `.js` de entrada llevan `?v=` en la plantilla; las hojas que
`app.css` importa y la fuente de iconos no pueden llevarlo (una URL dentro de un
`@import` es texto fijo). Sin una cabecera que lo diga, el navegador se queda con
la copia vieja de esas: se editaba `components.css` y la pantalla seguía pintando
como ayer, con los iconos como palabras.

De ahí la regla: con versión en la URL, cachea para siempre; sin versión,
revalida siempre — que con el ETag es un 304 y no cuesta nada.
"""

from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

INMUTABLE = "public, max-age=31536000, immutable"
REVALIDA = "no-cache"


class AssetFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        con_version = b"v=" in scope.get("query_string", b"")
        response.headers["Cache-Control"] = INMUTABLE if con_version else REVALIDA
        return response
