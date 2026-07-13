"""Nuevo cliente: ingesta Word → revisión humana → Client (flujo del Módulo 1)."""

from typing import Annotated, Any
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.compute_targets import compute_and_store_targets
from nutriplan.application.parse_intake import parse_intake
from nutriplan.domain.errors import IntakeAmbiguityError, LLMError
from nutriplan.domain.models import (
    ActivityLevel,
    Client,
    Goal,
    IntakeStatus,
    Sex,
)
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import container_of, db_session, render, repos_of, tenant_of

router = APIRouter()
logger = structlog.get_logger(__name__)

# Objetivo en texto libre del intake → Goal (heurística mínima; el entrenador
# siempre confirma en el formulario).
GOAL_HINTS = [
    (Goal.LOSE_FAT, ("bajar", "grasa", "adelgazar", "definir", "perder")),
    (Goal.GAIN_MUSCLE, ("masa", "ganar", "volumen", "subir")),
]

_SEX_ALIASES = {
    "mujer": Sex.FEMALE,
    "f": Sex.FEMALE,
    "hombre": Sex.MALE,
    "m": Sex.MALE,
}


def _safe_uuid(raw: str) -> UUID | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _safe_uuids(raw_ids: list[str]) -> list[UUID]:
    ids: list[UUID] = []
    for raw in raw_ids:
        parsed = _safe_uuid(raw)
        if parsed is not None:
            ids.append(parsed)
    return ids


def _coerce_sex(raw: str) -> Sex:
    try:
        return Sex(raw)
    except ValueError:
        alias = _SEX_ALIASES.get(raw.strip().lower())
        if alias is not None:
            return alias
        raise ValueError(f"Sexo no válido: {raw}") from None


def _coerce_goal(raw: str) -> Goal:
    try:
        return Goal(raw)
    except ValueError as exc:
        raise ValueError(f"Objetivo no válido: {raw}") from exc


def _coerce_activity(raw: str) -> ActivityLevel:
    try:
        return ActivityLevel(raw)
    except ValueError as exc:
        raise ValueError(f"Actividad no válida: {raw}") from exc

RESTRICTION_HINTS = {
    "no_seafood": ("marisco",),
    "no_dairy": ("lácteo", "lacteo", "lactosa"),
    "no_gluten": ("gluten",),
    "no_shake": ("sin malteada", "sin batido", "no toma proteína en polvo"),
}


def guess_goal(goal_raw: str) -> Goal:
    text = goal_raw.lower()
    for goal, words in GOAL_HINTS:
        if any(w in text for w in words):
            return goal
    return Goal.MAINTAIN


def guess_restrictions(parsed: dict[str, Any]) -> list[str]:
    raw = " · ".join(parsed.get("restrictions_raw", [])).lower()
    found = [key for key, words in RESTRICTION_HINTS.items() if any(w in raw for w in words)]
    if parsed.get("uses_protein_shake") is False and "no_shake" not in found:
        found.append("no_shake")
    return found


async def _food_groups(request: Request, session: AsyncSession,
                       selected: set[str]) -> list[dict[str, Any]]:
    """Chips del catálogo agrupados por categoría, marcando los seleccionados."""
    repos = repos_of(request, session)
    universe = await repos.foods.list_universe()
    groups = []
    for meta in presenter.FOOD_GROUPS:
        items = [
            {"id": str(f.id), "name": f.name_es.capitalize(), "on": str(f.id) in selected}
            for f in sorted(universe, key=lambda f: f.name_es)
            if f.category == meta["cat"]
        ]
        if items:
            groups.append({**meta, "items": items})
    return groups


async def _render_client_form_error(
    request: Request,
    session: AsyncSession,
    message: str,
    *,
    name: str,
    sex: str,
    age_years: int,
    height_cm: float,
    weight_kg: float,
    goal: str,
    activity_level: str,
    restrictions: list[str],
    food_ids: list[str],
    intake_id: str,
) -> HTMLResponse:
    """Devuelve el formulario con error en lugar de un 500 opaco (HTMX)."""
    form = {
        "intake_id": intake_id,
        "name": name,
        "sex": sex,
        "age_years": age_years,
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "goal": goal,
        "activity_level": activity_level,
        "restrictions": restrictions,
    }
    return render(
        request,
        "partials/intake_review.html",
        form=form,
        error=message,
        groups=await _food_groups(request, session, set(food_ids)),
    )


@router.get("/clientes/nuevo", response_class=HTMLResponse)
async def new_client_page(request: Request,
                          session: Annotated[AsyncSession, Depends(db_session)]) -> HTMLResponse:
    return render(request, "new_client.html", active_tab="clientes",
                  form=None, groups=await _food_groups(request, session, set()))


@router.post("/intake", response_class=HTMLResponse)
async def upload_intake(request: Request,
                        session: Annotated[AsyncSession, Depends(db_session)],
                        archivo: UploadFile) -> HTMLResponse:
    container = container_of(request)
    repos = repos_of(request, session)
    error = None
    if container.llm_client is None:
        error = ("La ingesta de Word necesita ANTHROPIC_API_KEY en el .env. "
                 "Puedes crear el cliente manualmente aquí abajo.")
    else:
        try:
            doc = await parse_intake(
                file_bytes=await archivo.read(),
                filename=archivo.filename or "intake.docx",
                tenant_id=tenant_of(request),
                llm=container.llm_client,
                intake_repo=repos.intakes,
                food_repo=repos.foods,
                prompts_dir=container.settings.prompts_dir,
                model=container.settings.llm_model_ingest,
            )
            parsed = doc.parsed
            selected = set(parsed.get("food_matches", {}).values())
            form = {
                "intake_id": str(doc.id),
                "name": parsed.get("name") or "",
                "sex": (parsed.get("sex") or Sex.FEMALE.value),
                "age_years": parsed.get("age_years") or "",
                "height_cm": parsed.get("height_cm") or "",
                "weight_kg": parsed.get("weight_kg") or "",
                "goal": guess_goal(parsed.get("goal_raw", "")).value,
                "goal_raw": parsed.get("goal_raw", ""),
                "activity_level": ActivityLevel.MODERATE.value,
                "training_raw": parsed.get("training_raw") or "",
                "restrictions": guess_restrictions(parsed),
                "ambiguities": doc.ambiguities,
                "unmatched": parsed.get("unmatched_foods", []),
                "filename": doc.source_filename,
            }
            return render(request, "partials/intake_review.html", form=form,
                          groups=await _food_groups(request, session, selected))
        except (IntakeAmbiguityError, LLMError) as exc:
            error = str(exc)
    return render(request, "partials/intake_review.html", form=None, error=error,
                  groups=await _food_groups(request, session, set()))


@router.post("/clientes", response_model=None)
async def create_client(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    name: Annotated[str, Form()],
    sex: Annotated[str, Form()],
    age_years: Annotated[int, Form()],
    height_cm: Annotated[float, Form()],
    weight_kg: Annotated[float, Form()],
    goal: Annotated[str, Form()] = Goal.MAINTAIN.value,
    activity_level: Annotated[str, Form()] = ActivityLevel.MODERATE.value,
    restrictions: Annotated[list[str], Form()] = [],  # noqa: B006
    food_ids: Annotated[list[str], Form()] = [],  # noqa: B006
    intake_id: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    container = container_of(request)
    repos = repos_of(request, session)

    form_kwargs = {
        "name": name.strip(),
        "sex": sex,
        "age_years": age_years,
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "goal": goal,
        "activity_level": activity_level,
        "restrictions": restrictions,
        "food_ids": food_ids,
        "intake_id": intake_id,
    }

    try:
        sex_v = _coerce_sex(sex)
        goal_v = _coerce_goal(goal)
        activity_v = _coerce_activity(activity_level)
    except ValueError as exc:
        return await _render_client_form_error(request, session, str(exc), **form_kwargs)

    try:
        requested_foods = _safe_uuids(food_ids)
        known_foods = await repos.foods.get_by_ids(requested_foods)
        liked_food_ids = [f.id for f in known_foods]

        client = Client(
            id=uuid4(),
            tenant_id=tenant_of(request),
            name=name.strip(),
            sex=sex_v,
            age_years=age_years,
            height_cm=height_cm,
            weight_kg=weight_kg,
            goal=goal_v,
            activity_level=activity_v,
            liked_food_ids=liked_food_ids,
            restrictions=restrictions,
            notes=notes.strip() or None,
        )
        await repos.clients.add(client)
        await compute_and_store_targets(
            client=client, config_provider=container.config_provider, targets_repo=repos.targets
        )
        if parsed_intake_id := _safe_uuid(intake_id):
            doc = await repos.intakes.get(parsed_intake_id)
            if doc:
                await repos.intakes.update_status(
                    doc.id, IntakeStatus.CONFIRMED,
                    parsed={**doc.parsed, "client_id": str(client.id)},
                )
    except Exception as exc:
        await session.rollback()
        logger.exception("create_client_failed", error=str(exc))
        return await _render_client_form_error(
            request,
            session,
            "No se pudo crear el cliente. Revisa los datos e inténtalo de nuevo.",
            **form_kwargs,
        )

    response = RedirectResponse(f"/generador?cliente={client.id}", status_code=303)
    if request.headers.get("HX-Request"):
        response.headers["HX-Redirect"] = f"/generador?cliente={client.id}"
    return response
