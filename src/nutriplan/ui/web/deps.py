"""Infraestructura compartida de la UI web: plantillas, sesión y contexto."""

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.analytics import Event, track
from nutriplan.container import Container, Repos
from nutriplan.domain.models import Account, Role
from nutriplan.ui.web import format as fmt
from nutriplan.ui.web import presenter, week_view
from nutriplan.ui.web.security import csrf_token

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

def _asset_version() -> int:
    """Versión de los estáticos, para que el navegador no sirva una copia vieja.

    Mira TODOS los archivos, no solo `app.css`: los estilos viven en los que
    este importa, y esos no llevan versión en la URL. Mirando solo el de
    entrada, editar `tokens.css` no invalidaba nada y el navegador seguía
    pintando con el CSS anterior.
    """
    files = [*STATIC_DIR.glob("*.css"), *STATIC_DIR.glob("*.js"),
             *(STATIC_DIR / "styles").glob("*.css")]
    return int(max(f.stat().st_mtime for f in files))


ASSET_VERSION = _asset_version()

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals.update(
    asset_v=ASSET_VERSION,
    soft_of=fmt.soft_of,
    initials=presenter.initials,
    GOAL_META=presenter.GOAL_META,
    MACRO_META=presenter.MACRO_META,
    RESTRICTION_TOGGLES=presenter.RESTRICTION_TOGGLES,
    MEAL_TOGGLES=presenter.MEAL_TOGGLES,
    ACTIVITY_LABELS=presenter.ACTIVITY_LABELS,
    SEX_LABELS=presenter.SEX_LABELS,
    DAY_SHORT=fmt.DAY_SHORT,
    SLOT_META=week_view.SLOT_META,
)


def safe_uuid(raw: str) -> UUID:
    """Un id de la URL. Si no es un UUID es culpa de quien lo escribió: 400.

    Antes `UUID(raw)` reventaba en un 500 con traza, que además le contaba al
    curioso más de lo que debería sobre el servidor.
    """
    try:
        return UUID(raw.strip())
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="Identificador no válido") from None


def container_of(request: Request) -> Container:
    return request.app.state.container  # type: ignore[no-any-return]


def tenant_or_none(request: Request) -> UUID | None:
    """El tenant, o None si no hay sesión.

    Solo para presentación —la marca de la página de login, por ejemplo—, donde
    no tener tenant es normal y hay un valor por defecto razonable. Para leer o
    escribir datos se usa `tenant_of`, que no adivina.
    """
    raw = request.session.get("tenant_id") if "session" in request.scope else None
    if not raw:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def tenant_of(request: Request) -> UUID:
    """El tenant de quien está en sesión.

    Antes caía al tenant semilla cuando no había sesión. Con rutas públicas
    nuevas eso significaba que cualquiera heredaba los datos de la cuenta
    local: los repos filtraban bien, pero por el tenant equivocado. Ahora
    revienta, que es lo que un fallo de autorización debe hacer.
    """
    raw = request.session.get("tenant_id") if "session" in request.scope else None
    if not raw:
        raise RuntimeError("Ruta protegida sin tenant en sesión")
    try:
        return UUID(str(raw))
    except ValueError:
        raise RuntimeError("El tenant de la sesión no es un UUID") from None


def current_account(request: Request) -> dict[str, str] | None:
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


async def acting_account(request: Request, session: AsyncSession) -> Account | None:
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


async def track_event(
    request: Request, session: AsyncSession, event: Event, **props: object
) -> None:
    """Registra un evento del embudo si esa persona dio su consentimiento.

    Se resuelve aquí y no en cada ruta para que sea imposible olvidarse del
    permiso: la comprobación vive en un solo sitio.
    """
    container = container_of(request)
    email = request.session.get("email") if "session" in request.scope else None
    account = None
    if email:
        account = await container.auth_repo(session).get_by_email_any_provider(str(email))
    await track(
        event,
        repo=container.event_repo(session),
        has_consent=account is not None and account.consent_analytics_at is not None,
        user_id=account.id if account else None,
        tenant_id=account.tenant_id if account else None,
        platform=request.headers.get("X-Platform", "web")[:20],
        **props,
    )


def repos_of(request: Request, session: AsyncSession) -> Repos:
    return container_of(request).repos(session, tenant_of(request))


def render(request: Request, template: str, **context: object) -> HTMLResponse:
    """Render con el contexto de marca que toda plantilla necesita."""
    container = container_of(request)
    branding = container.branding(tenant_or_none(request))
    settings = container.settings
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={
            "branding": branding,
            "iniciales": presenter.initials(branding.tenant_name),
            "cuenta": current_account(request),
            "role": role_of(request),
            "csrf_token": csrf_token(request),
            "offline": container.llm_client is None,
            "google_client_id": settings.google_client_id,
            "apple_client_id": settings.apple_client_id,
            **context,
        },
    )
