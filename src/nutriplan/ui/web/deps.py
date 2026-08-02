"""Infraestructura compartida de la UI web: plantillas, sesión y contexto."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.seed import DEFAULT_TENANT_ID
from nutriplan.container import Container, Repos
from nutriplan.domain.models import Account, Role
from nutriplan.ui.web import format as fmt
from nutriplan.ui.web import presenter

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Cache-busting del CSS: al cambiar el archivo cambia su mtime, así que la URL del
# `<link>` cambia y el navegador re-descarga en vez de quedarse con una copia vieja.
ASSET_VERSION = int((STATIC_DIR / "app.css").stat().st_mtime)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals.update(
    asset_v=ASSET_VERSION,
    soft_of=fmt.soft_of,
    initials=presenter.initials,
    BRAND_SWATCHES=presenter.BRAND_SWATCHES,
    GOAL_META=presenter.GOAL_META,
    MACRO_META=presenter.MACRO_META,
    RESTRICTION_TOGGLES=presenter.RESTRICTION_TOGGLES,
    MEAL_TOGGLES=presenter.MEAL_TOGGLES,
    ACTIVITY_LABELS=presenter.ACTIVITY_LABELS,
    SEX_LABELS=presenter.SEX_LABELS,
    DAY_SHORT=fmt.DAY_SHORT,
    SLOT_META=presenter.SLOT_META,
)


def container_of(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def tenant_of(request: Request) -> UUID:
    """Tenant del entrenador logueado (de la cookie de sesión).

    Sin sesión cae al tenant por defecto (semilla local); las rutas protegidas
    ya redirigen a /login antes de llegar aquí.
    """
    raw = request.session.get("tenant_id") if "session" in request.scope else None
    if raw:
        try:
            return UUID(raw)
        except ValueError:
            pass
    return DEFAULT_TENANT_ID


def current_trainer(request: Request) -> dict[str, str] | None:
    sess = request.session if "session" in request.scope else {}
    if sess.get("tenant_id"):
        return {"name": sess.get("name", ""), "email": sess.get("email", ""),
                "role": sess.get("role", "user")}
    return None


def account_id_of(request: Request) -> UUID:
    """La cuenta en sesión. El guard global ya garantizó que hay una."""
    raw = request.session.get("user_id") if "session" in request.scope else None
    if not raw:
        raise RuntimeError("Ruta protegida sin cuenta en sesión")
    return UUID(str(raw))


def role_of(request: Request) -> str:
    sess = request.session if "session" in request.scope else {}
    return str(sess.get("role", "")) if sess else ""


def is_super_user(request: Request) -> bool:
    """El super_user no tiene cupos: pasa de largo todo el enforcement."""
    return role_of(request) == Role.SUPER_USER


async def acting_trainer(request: Request, session: AsyncSession) -> Account | None:
    """La cuenta logueada, con sus cupos, para el enforcement. `None` si no hay sesión."""
    email = request.session.get("email") if "session" in request.scope else None
    if not email:
        return None
    repo = container_of(request).auth_repo(session)
    return await repo.get_by_email_any_provider(str(email))


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Sesión por request; commit al final si la vista no falló."""
    container = container_of(request)
    async with container.session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def repos_of(request: Request, session: AsyncSession) -> Repos:
    return container_of(request).repos(session, tenant_of(request))


def render(request: Request, template: str, **context: object) -> HTMLResponse:
    """Render con el contexto de marca que toda plantilla necesita."""
    container = container_of(request)
    branding = container.branding(tenant_of(request))
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "branding": branding,
            "brand": branding.primary_color,
            "brand_soft": fmt.soft_of(branding.primary_color),
            "trainer_initials": presenter.initials(branding.tenant_name),
            "trainer": current_trainer(request),
            "role": role_of(request),
            "offline": container.llm_client is None,
            **context,
        },
    )
