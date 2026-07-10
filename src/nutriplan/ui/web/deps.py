"""Infraestructura compartida de la UI web: plantillas, sesión y contexto."""

from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

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
    ACTIVITY_LABELS=presenter.ACTIVITY_LABELS,
    SEX_LABELS=presenter.SEX_LABELS,
    DAY_SHORT=presenter.DAY_SHORT,
)


def container_of(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Sesión por request; commit al final si la vista no falló."""
    container = container_of(request)
    async with container.session_factory() as session:
        yield session
        await session.commit()


def repos_of(request: Request, session: AsyncSession) -> Repos:
    return container_of(request).repos(session)


def render(request: Request, template: str, **context: object) -> HTMLResponse:
    """Render con el contexto de marca que toda plantilla necesita."""
    container = container_of(request)
    branding = container.branding()
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "branding": branding,
            "brand": branding.primary_color,
            "brand_soft": presenter.soft_of(branding.primary_color),
            "trainer_initials": presenter.initials(branding.tenant_name),
            "offline": container.llm_client is None,
            **context,
        },
    )
