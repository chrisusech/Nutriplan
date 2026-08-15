"""El tope de peticiones: qué se frena, qué no, y a quién se le cuenta.

La regla de negocio es corta: leer una pantalla no gasta cupo, pero pedir algo
que por dentro llama al proveedor de pago sí. Antes el middleware dejaba pasar
todo GET, así que la ruta más cara de la app era la única sin freno.
"""

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from nutriplan.ui.web.security import (
    READ_RATE_LIMITS,
    RateLimiter,
    rate_limit_middleware,
)


def _app() -> TestClient:
    app = FastAPI()

    @app.get("/menu/receta")
    async def receta() -> PlainTextResponse:
        return PlainTextResponse("ok")

    @app.get("/login")
    async def login() -> PlainTextResponse:
        return PlainTextResponse("ok")

    app.middleware("http")(rate_limit_middleware(RateLimiter()))
    app.add_middleware(SessionMiddleware, secret_key="x" * 32)
    return TestClient(app)


def test_pedir_recetas_sin_parar_acaba_recibiendo_un_429() -> None:
    """Cada receta que falta es una llamada facturada: repetirla no es gratis."""
    cliente = _app()
    tope = READ_RATE_LIMITS["/menu/receta"][0]
    codes = [cliente.get("/menu/receta").status_code for _ in range(tope + 5)]

    assert codes[0] == 200
    assert codes[-1] == 429


def test_navegar_la_semana_completa_no_dispara_el_limite() -> None:
    """Siete días por cinco comidas: el uso normal tiene que caber holgado."""
    cliente = _app()
    codes = [cliente.get("/menu/receta").status_code for _ in range(7 * 5)]
    assert set(codes) == {200}


def test_abrir_la_pantalla_de_login_nunca_gasta_cupo() -> None:
    """El tope de /login es para los envíos, no para ver el formulario."""
    cliente = _app()
    codes = [cliente.get("/login").status_code for _ in range(50)]
    assert set(codes) == {200}


def test_el_diccionario_del_limitador_no_crece_para_siempre() -> None:
    """Con las IP rotando de móvil, cada visitante dejaba un hueco vivo."""
    limiter = RateLimiter()
    for i in range(200):
        limiter.check("/login", f"ip:10.0.0.{i}", limit=5, window_s=300)

    limiter.purge(older_than_s=0)
    assert limiter._hits == {}
