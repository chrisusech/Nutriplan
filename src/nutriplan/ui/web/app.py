"""App FastAPI de la UI (Nivel 1): multi-entrenador con login por sesión.

Sirve las plantillas del diseño "Generador Nutricional" y llama los casos de
uso en proceso vía el container. Arranque:

    uv run nutriplan
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from nutriplan.adapters.db.migrate import upgrade_to_head_async
from nutriplan.adapters.db.models import TenantRow
from nutriplan.adapters.db.seed import seed_local
from nutriplan.config.settings import Environment
from nutriplan.container import Container, build_container
from nutriplan.ui.web.deps import container_of

STATIC_DIR = Path(__file__).parent / "static"
logger = structlog.get_logger(__name__)

# Rutas accesibles sin sesión (login, alta, estáticos).
_PUBLIC_PREFIXES = (
    "/login",
    "/signup",
    "/recuperar",
    "/static",
    "/logout",
    "/docs",
    "/openapi.json",
)


def create_app(container: Container | None = None) -> FastAPI:
    container = container or build_container()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # El esquema sale de las migraciones, nunca de create_all: si la
        # migración no lo tiene, no existe.
        #
        # En prod NO se migra al arrancar: Alembic necesita una sesión y la app
        # va por el transaction pooler de Supabase, y además varias instancias
        # arrancando a la vez competirían por el lock. Allí `alembic upgrade
        # head` es un paso de despliegue, contra la conexión directa (5432).
        if (
            container.settings.env is Environment.LOCAL
            and container.settings.run_migrations_on_start
        ):
            logger.info("startup_migrations_begin")
            await upgrade_to_head_async(container.settings.database_url)
            logger.info("startup_migrations_done")
        logger.info("startup_seed_begin")
        async with container.session_factory() as session:
            csv_path = container.settings.project_root / "data" / "foods" / "curated_foods.csv"
            await seed_local(session, csv_path)
            await session.commit()
        logger.info("startup_seed_done")
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

        tenant_raw = request.session.get("tenant_id")
        if tenant_raw:
            try:
                tenant_id = UUID(str(tenant_raw))
            except ValueError:
                request.session.clear()
                return RedirectResponse("/login?sesion=expirada", status_code=303)
            container = container_of(request)
            async with container.session_factory() as session:
                if await session.get(TenantRow, tenant_id) is None:
                    request.session.clear()
                    return RedirectResponse("/login?sesion=expirada", status_code=303)

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
