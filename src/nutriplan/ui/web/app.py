"""App FastAPI de la UI (Nivel 1): multi-entrenador con login por sesión.

Sirve las plantillas del diseño "Generador Nutricional" y llama los casos de
uso en proceso vía el container. Arranque:

    uv run nutriplan
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from nutriplan.adapters.db.seed import seed_local
from nutriplan.adapters.db.session import Base
from nutriplan.container import Container, build_container

STATIC_DIR = Path(__file__).parent / "static"

# Rutas accesibles sin sesión (login, alta, estáticos).
_PUBLIC_PREFIXES = ("/login", "/signup", "/static", "/logout", "/docs", "/openapi.json")


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Nivel 1: esquema + seed idempotentes al arrancar (SQLite local).
        async with container.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with container.session_factory() as session:
            csv_path = container.settings.project_root / "data" / "foods" / "curated_foods.csv"
            await seed_local(session, csv_path)
            await session.commit()
        app.state.jobs_in_flight = set()
        yield
        await container.engine.dispose()

    app = FastAPI(title="NutriPlan", lifespan=lifespan)
    app.state.container = container

    # El guard se registra ANTES que SessionMiddleware para que este quede por
    # fuera (add_middleware inserta al frente): así request.session ya existe
    # cuando el guard corre.
    @app.middleware("http")
    async def require_login(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Guard por sesión y por rol.

        Sin sesión → /login (salvo rutas públicas). El cliente solo ve su portal;
        las rutas de admin exigen rol admin; el entrenador/admin no entra al portal.
        """
        path = request.url.path
        if path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)
        if not request.session.get("tenant_id"):
            return RedirectResponse("/login", status_code=303)

        role = request.session.get("role", "trainer")
        if role == "client":
            if not path.startswith("/portal"):
                return RedirectResponse("/portal", status_code=303)
        else:  # entrenador o admin
            if path.startswith("/portal"):
                return RedirectResponse("/", status_code=303)
            if path.startswith("/admin") and role != "admin":
                return RedirectResponse("/", status_code=303)
        return await call_next(request)

    app.add_middleware(SessionMiddleware, secret_key=container.settings.session_secret)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from nutriplan.ui.web.routes import (
        auth,
        dashboard,
        generator,
        intake,
        plans,
        portal,
        recipes,
    )

    app.include_router(auth.router)
    app.include_router(dashboard.router)
    app.include_router(intake.router)
    app.include_router(generator.router)
    app.include_router(plans.router)
    app.include_router(recipes.router)
    app.include_router(portal.router)
    return app


def main() -> None:
    """Entrypoint de consola (`uv run nutriplan`)."""
    import uvicorn

    uvicorn.run("nutriplan.ui.web.app:create_app", factory=True, host="127.0.0.1", port=8000)
