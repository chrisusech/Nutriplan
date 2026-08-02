"""Perfil y opiniones: lo que la persona hace fuera de su menú."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.ui.web.deps import account_id_of, db_session, render, repos_of

router = APIRouter()

FEEDBACK_CATEGORIES = [
    ("receta", "Sobre un plato"),
    ("idea", "Una idea"),
    ("bug", "Algo no funciona"),
    ("general", "Otra cosa"),
]
MAX_MESSAGE = 4000


@router.get("/perfil", response_class=HTMLResponse)
async def profile(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse:
    repos = repos_of(request, session)
    client = await repos.clients.get_by_user(account_id_of(request))
    return render(request, "profile.html", active_tab="perfil", perfil=client)


@router.get("/feedback", response_class=HTMLResponse)
async def feedback_page(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse:
    return render(
        request,
        "feedback.html",
        active_tab="feedback",
        categories=FEEDBACK_CATEGORIES,
        enviado=request.query_params.get("gracias") == "1",
    )


@router.post("/feedback", response_model=None)
async def submit_feedback(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    category: Annotated[str, Form()],
    message: Annotated[str, Form()],
    nps: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Lo que la gente tiene para decir. Es medio producto del BETA."""
    texto = message.strip()[:MAX_MESSAGE]
    if not texto:
        return render(
            request, "feedback.html", active_tab="feedback",
            categories=FEEDBACK_CATEGORIES,
            error="Escríbenos algo, aunque sea corto.",
        )
    valid = {key for key, _ in FEEDBACK_CATEGORIES}
    await repos_of(request, session).ratings.add_feedback(
        user_id=account_id_of(request),
        category=category if category in valid else "general",
        message=texto,
        nps=int(nps) if nps.isdigit() and 0 <= int(nps) <= 10 else None,
        platform=request.headers.get("X-Platform", "web")[:20],
    )
    return RedirectResponse("/feedback?gracias=1", status_code=303)
