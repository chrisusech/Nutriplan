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
from nutriplan.ui.web import presenter

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals.update(
    soft_of=presenter.soft_of,
    initials=presenter.initials,
    BRAND_SWATCHES=presenter.BRAND_SWATCHES,
    GOAL_META=presenter.GOAL_META,
    MACRO_META=presenter.MACRO_META,
    RESTRICTION_TOGGLES=presenter.RESTRICTION_TOGGLES,
    MEAL_TOGGLES=presenter.MEAL_TOGGLES,
    ACTIVITY_LABELS=presenter.ACTIVITY_LABELS,
    SEX_LABELS=presenter.SEX_LABELS,
    DAY_SHORT=presenter.DAY_SHORT,
    SLOT_META=presenter.SLOT_META,
    PHASE_LABELS=presenter.PHASE_LABELS,
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
                "role": sess.get("role", "trainer")}
    return None


def role_of(request: Request) -> str:
    sess = request.session if "session" in request.scope else {}
    return str(sess.get("role", "")) if sess else ""


def client_id_of(request: Request) -> UUID | None:
    """El Client asociado a la cuenta logueada (solo cuentas de cliente)."""
    raw = request.session.get("client_id") if "session" in request.scope else None
    if raw:
        try:
            return UUID(raw)
        except ValueError:
            pass
    return None


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
            "brand_soft": presenter.soft_of(branding.primary_color),
            "trainer_initials": presenter.initials(branding.tenant_name),
            "trainer": current_trainer(request),
            "role": role_of(request),
            "offline": container.llm_client is None,
            **context,
        },
    )
