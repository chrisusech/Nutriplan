"""Onboarding: la persona se describe a sí misma y obtiene su perfil.

Reemplaza al intake de Word y al importador del formulario. El corazón es el
relato libre —"¿cómo es un día típico tuyo?"—, no una lista de ingredientes que
marcar: eso último era lo que agobiaba.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.analytics import Event
from nutriplan.application.onboarding import create_profile
from nutriplan.domain.errors import ValidationError
from nutriplan.domain.food_filter import RESTRICTION_TAG_MAP
from nutriplan.domain.models import (
    CORE_MEAL_SLOTS,
    ActivityLevel,
    Goal,
    MealSlot,
    Sex,
)
from nutriplan.ui.web import presenter
from nutriplan.ui.web.deps import (
    account_id_of,
    container_of,
    db_session,
    render,
    repos_of,
    tenant_of,
    track_event,
)

router = APIRouter()

_SEX_ALIASES = {"mujer": Sex.FEMALE, "f": Sex.FEMALE, "hombre": Sex.MALE, "m": Sex.MALE}


def _coerce(enum: Any, raw: str, label: str) -> Any:
    try:
        return enum(raw)
    except ValueError:
        raise ValidationError(f"{label} no válido: {raw}") from None


def _coerce_sex(raw: str) -> Sex:
    try:
        return Sex(raw)
    except ValueError:
        alias = _SEX_ALIASES.get(raw.strip().lower())
        if alias is None:
            raise ValidationError(f"Sexo no válido: {raw}") from None
        return alias


def _safe_uuids(raw_ids: list[str]) -> list[UUID]:
    ids: list[UUID] = []
    for raw in raw_ids:
        try:
            ids.append(UUID(raw.strip()))
        except ValueError:
            continue
    return ids


def _restricciones(raw: list[str]) -> list[str]:
    """Solo las que el filtro de alimentos sabe traducir a tags.

    `allowed_foods` se niega —con razón— a adivinar qué prohíbe una restricción
    que no conoce, así que dejar pasar una cadena cualquiera desde el formulario
    convertía la generación en un 500 varios pasos más adelante.
    """
    return [r for r in raw if r in RESTRICTION_TAG_MAP]


def _meal_slots(raw: list[str]) -> list[MealSlot]:
    """Las comidas elegidas; las tres principales no son opcionales."""
    chosen = []
    for value in raw:
        try:
            chosen.append(MealSlot(value))
        except ValueError:
            continue
    for core in CORE_MEAL_SLOTS:
        if core not in chosen:
            chosen.append(core)
    return [s for s in MealSlot if s in chosen]


async def _food_groups(
    request: Request, session: AsyncSession, selected: set[str]
) -> list[dict[str, Any]]:
    """Chips del catálogo por categoría. Marcar es opcional: vacío = sorpréndeme."""
    universe = await repos_of(request, session).foods.list_universe()
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


@router.get("/onboarding", response_model=None)
async def onboarding_page(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    existing = await repos.clients.get_by_user(account_id_of(request))
    if existing is not None:
        return RedirectResponse("/", status_code=303)
    cuenta = await container_of(request).auth_repo(session).get_by_id(account_id_of(request))
    return render(
        request, "onboarding.html", active_tab="onboarding",
        # El nombre ya lo escribió al registrarse: repetir la pregunta en el
        # primer campo del primer paso es la peor primera impresión posible.
        form={"name": cuenta.name, "restrictions": []} if cuenta else None,
        groups=await _food_groups(request, session, set()),
    )


@router.post("/onboarding", response_model=None)
async def submit_onboarding(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    name: Annotated[str, Form()],
    sex: Annotated[str, Form()],
    age_years: Annotated[int, Form()],
    height_cm: Annotated[float, Form()],
    weight_kg: Annotated[float, Form()],
    goal: Annotated[str, Form()],
    activity_level: Annotated[str, Form()],
    meal_slots: Annotated[list[str] | None, Form()] = None,
    food_ids: Annotated[list[str] | None, Form()] = None,
    restrictions: Annotated[list[str] | None, Form()] = None,
    context_tags: Annotated[list[str] | None, Form()] = None,
    dislikes: Annotated[str, Form()] = "",
    eating_pattern_raw: Annotated[str, Form()] = "",
    city: Annotated[str, Form()] = "",
    country: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    repos = repos_of(request, session)
    account_id = account_id_of(request)
    meal_slots, food_ids = meal_slots or [], food_ids or []
    restrictions, context_tags = restrictions or [], context_tags or []
    # El nombre vive en la cuenta: si aquí lo corrigen, se corrige allá.
    await container_of(request).auth_repo(session).set_name(account_id, name)
    try:
        client = await create_profile(
            account_id=account_id,
            tenant_id=tenant_of(request),
            name=name,
            sex=_coerce_sex(sex),
            age_years=age_years,
            height_cm=height_cm,
            weight_kg=weight_kg,
            goal=_coerce(Goal, goal, "Objetivo"),
            activity_level=_coerce(ActivityLevel, activity_level, "Nivel de actividad"),
            meal_slots=_meal_slots(meal_slots),
            city=city,
            country=country,
            liked_food_ids=_safe_uuids(food_ids),
            restrictions=_restricciones(restrictions),
            dislikes=[d for d in dislikes.replace("\n", ",").split(",") if d.strip()],
            context_tags=context_tags,
            eating_pattern_raw=eating_pattern_raw,
            client_repo=repos.clients,
        )
    except (ValidationError, ValueError) as exc:
        return render(
            request, "onboarding.html", active_tab="onboarding", error=str(exc),
            form={
                "name": name, "sex": sex, "age_years": age_years,
                "height_cm": height_cm, "weight_kg": weight_kg, "goal": goal,
                "activity_level": activity_level, "restrictions": restrictions,
                "eating_pattern_raw": eating_pattern_raw,
            },
            groups=await _food_groups(request, session, set(food_ids)),
        )
    await track_event(
        request, session, Event.ONBOARDING_DONE,
        comidas=len(client.meal_slots),
        marco_alimentos=len(client.liked_food_ids),
        conto_habitos=client.eating_pattern_raw is not None,
        contexto=client.context_tags,
        objetivo=client.goal.value,
        tiene_ciudad=client.city is not None,
    )
    return RedirectResponse("/", status_code=303)
