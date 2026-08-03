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
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from nutriplan.adapters.db.migrate import upgrade_to_head_async
from nutriplan.adapters.db.models import TenantRow
from nutriplan.adapters.db.seed import seed_local
from nutriplan.application.auth import SignupError, register
from nutriplan.config.settings import Environment
from nutriplan.container import Container, build_container
from nutriplan.domain.models import Role
from nutriplan.ui.web.deps import container_of
from nutriplan.ui.web.security import (
    RateLimiter,
    csrf_middleware,
    rate_limit_middleware,
    security_headers_middleware,
)

STATIC_DIR = Path(__file__).parent / "static"
logger = structlog.get_logger(__name__)

# Rutas accesibles sin sesión. El alta es pública: cualquiera se registra.
_PUBLIC_PREFIXES = (
    "/login",
    "/registro",
    "/verificar",
    "/auth/oauth",
    "/privacidad",
    "/terminos",
    "/soporte",
    "/health",
    "/recuperar",
    "/static",
    "/logout",
    "/docs",
    "/openapi.json",
)


async def _seed_super_user(container: Container, session: AsyncSession) -> None:
    """Siembra el super_user desde settings si aún no existe. Idempotente."""
    email = container.settings.admin_email.strip().lower()
    password = container.settings.admin_password
    if not email or not password:
        return
    auth_repo = container.auth_repo(session)
    if await auth_repo.get_by_email(email) is not None:
        return
    try:
        await register(
            name="Administrador", email=email, password=password, auth_repo=auth_repo,
            branding_dir=container.settings.branding_dir, role=Role.SUPER_USER,
        )
        logger.info("super_user_seeded", email=email)
    except SignupError as exc:
        logger.warning("super_user_seed_skipped", reason=str(exc))


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
            await _seed_super_user(container, session)
            await session.commit()
        logger.info("startup_seed_done")
        app.state.jobs_in_flight = set()
        yield
        await container.engine.dispose()

    prod = container.settings.env is Environment.PROD
    # En producción no se publica el mapa de rutas: es el primer regalo a un
    # escáner automático.
    app = FastAPI(
        title="NutriPlan",
        lifespan=lifespan,
        docs_url=None if prod else "/docs",
        redoc_url=None,
        openapi_url=None if prod else "/openapi.json",
    )
    app.state.container = container
    app.state.rate_limiter = RateLimiter()

    # El guard se registra ANTES que SessionMiddleware para que este quede por
    # fuera (add_middleware inserta al frente): así request.session ya existe
    # cuando el guard corre.
    @app.middleware("http")
    async def require_login(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Guard por sesión y por rol.

        Sin sesión → /login (salvo rutas públicas). El área /admin exige rol
        super_user; el entrenador (user) entra a todo lo demás.
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

        # El rol NO se cree de la cookie: bloquear a alguien tiene que echarlo
        # ya, no en catorce días cuando le caduque la sesión.
        if path.startswith("/admin"):
            email = request.session.get("email")
            async with container_of(request).session_factory() as session:
                account = await container_of(request).auth_repo(session).get_by_email_any_provider(
                    str(email or "")
                )
            if account is None or not account.is_active or account.role != Role.SUPER_USER:
                return RedirectResponse("/", status_code=303)
        return await call_next(request)

    app.middleware("http")(csrf_middleware)
    app.middleware("http")(rate_limit_middleware(app.state.rate_limiter))
    app.middleware("http")(security_headers_middleware(https=prod))

    app.add_middleware(
        SessionMiddleware,
        secret_key=container.settings.session_secret,
        session_cookie="nutriplan_session",
        # Sin `Secure` la cookie viaja en claro por cualquier HTTP.
        https_only=prod,
        same_site="lax",
        max_age=14 * 24 * 3600,
    )
    if prod and container.settings.allowed_hosts:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=container.settings.allowed_hosts.split(","),
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> Response:
        """Un fallo nuestro no le cuenta al visitante cómo estamos por dentro."""
        logger.exception("unhandled_error", path=request.url.path, error=str(exc))
        return PlainTextResponse(
            "Algo se rompió de nuestro lado. Ya lo estamos mirando.", status_code=500
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness para Fly / balanceadores. Sin DB: un fallo de Postgres no
        debe tumbar el check de despliegue mientras reiniciamos."""
        return {"status": "ok"}

    from nutriplan.ui.web.routes import (
        account,
        admin,
        auth,
        menu,
        onboarding,
        week,
    )

    app.include_router(auth.router)
    app.include_router(week.router)
    app.include_router(account.router)
    app.include_router(onboarding.router)
    app.include_router(menu.router)
    app.include_router(admin.router)
    return app


def main() -> None:
    """Entrypoint de consola (`uv run nutriplan`)."""
    import uvicorn

    uvicorn.run("nutriplan.ui.web.app:create_app", factory=True, host="127.0.0.1", port=8000)
