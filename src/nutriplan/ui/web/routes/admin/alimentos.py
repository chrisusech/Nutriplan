"""Consola de alimentos: buscar, corregir, dar de alta o retirar."""

from typing import Annotated
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.food.catalog import CatalogEntry, to_food_item, validate_entries
from nutriplan.domain.errors import FoodNotFoundError, ValidationError
from nutriplan.domain.models import (
    CookingMethod,
    FoodCategory,
    FoodItem,
    FoodState,
    MealSlot,
    UnitGranularity,
)
from nutriplan.ui.web.deps import account_id_of, db_session, render, repos_of

router = APIRouter()

PAGE_SIZE = 50


def _back(**params: object) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v not in ("", None)})
    return RedirectResponse(f"/admin/alimentos?{query}" if query else "/admin/alimentos", 303)


@router.get("/admin/alimentos", response_class=HTMLResponse)
async def catalogo(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    q: str = "",
    categoria: str = "",
    nivel: str = "",
    pagina: int = 1,
    error: str = "",
    ok: str = "",
) -> HTMLResponse:
    repos = repos_of(request, session)
    page = max(pagina, 1)
    only_listable = {"nucleo": True, "fondo": False}.get(nivel)
    valid = {c.value for c in FoodCategory}
    foods, total = await repos.foods.list_for_console(
        query=q,
        category=FoodCategory(categoria) if categoria in valid else None,
        only_listable=only_listable,
        include_retired=True,
        limit=PAGE_SIZE,
        offset=(page - 1) * PAGE_SIZE,
    )
    return render(
        request,
        "admin_alimentos.html",
        active_tab="alimentos",
        alimentos=foods,
        total=total,
        q=q,
        categoria=categoria,
        nivel=nivel,
        pagina=page,
        paginas=max(1, -(-total // PAGE_SIZE)),
        categorias=list(FoodCategory),
        estados=list(FoodState),
        metodos=list(CookingMethod),
        granularidades=list(UnitGranularity),
        slots=list(MealSlot),
        error=error,
        ok=ok,
    )


def _float(value: str, campo: str) -> float | None:
    texto = value.strip().replace(",", ".")
    if not texto:
        return None
    try:
        return float(texto)
    except ValueError:
        raise ValidationError(f"{campo}: «{value}» no es un número") from None


def _requerido(value: str, campo: str) -> float:
    number = _float(value, campo)
    if number is None:
        raise ValidationError(f"Falta {campo}")
    return number


def _entry_from_form(form: dict[str, str], food: FoodItem | None) -> CatalogEntry:
    """Del formulario a una fila. Pasa por los mismos controles que el volcado USDA."""
    slots = [MealSlot(s) for s in form.get("meal_slots", "").split(";") if s.strip()]
    return CatalogEntry(
        id_override=food.id if food else None,
        fdc_id=int(form["fdc_id"]) if form.get("fdc_id", "").strip().isdigit() else None,
        source=form.get("source", "").strip() or ("curated" if food is None else food.source),
        name_es=form.get("name_es", "").strip(),
        name_en=form.get("name_en", "").strip() or None,
        category=FoodCategory(form["category"]),
        kcal_100g=_requerido(form.get("kcal_100g", ""), "las kcal"),
        protein_100g=_requerido(form.get("protein_100g", ""), "la proteína"),
        carb_100g=_requerido(form.get("carb_100g", ""), "el carbohidrato"),
        fat_100g=_requerido(form.get("fat_100g", ""), "la grasa"),
        fiber_100g=_float(form.get("fiber_100g", ""), "la fibra") or 0.0,
        state=FoodState(form.get("state", FoodState.NOT_APPLICABLE.value)),
        cooking_method=(
            CookingMethod(form["cooking_method"]) if form.get("cooking_method") else None
        ),
        yield_factor=_float(form.get("yield_factor", ""), "el factor de rendimiento"),
        tags=[t.strip() for t in form.get("tags", "").split(";") if t.strip()],
        aliases=[a.strip() for a in form.get("aliases", "").split(";") if a.strip()],
        meal_slots=slots,
        default_unit_g=_float(form.get("default_unit_g", ""), "la porción natural"),
        unit_granularity=UnitGranularity(form.get("unit_granularity", "grams")),
        unit_name=form.get("unit_name", "").strip() or None,
        portion_min_g=_float(form.get("portion_min_g", ""), "el mínimo"),
        portion_max_g=_float(form.get("portion_max_g", ""), "el máximo"),
        engine_default=form.get("engine_default") == "1",
        is_free=food.is_free if food else False,
        free_text=food.free_text if food else None,
    )


@router.post("/admin/alimentos/guardar", response_model=None)
async def guardar(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    food_id: Annotated[str, Form()] = "",
    q: Annotated[str, Form()] = "",
) -> RedirectResponse:
    repos = repos_of(request, session)
    form = {k: str(v) for k, v in (await request.form()).items()}
    try:
        existente: FoodItem | None = None
        if food_id.strip():
            found = await repos.foods.get_by_ids([UUID(food_id)])
            if not found:
                raise FoodNotFoundError("Ese alimento ya no está en la base")
            existente = found[0]

        entry = _entry_from_form(form, existente)
        _ok, rechazado = validate_entries([entry])
        if rechazado:
            raise ValidationError(rechazado[0][1])

        food = to_food_item(entry)
        editor = str(account_id_of(request))[:40]
        if existente is None:
            await repos.foods.upsert_globals([food])
            await repos.foods.update(food, edited_by=editor)
        else:
            await repos.foods.update(food, edited_by=editor)
        await session.commit()
    except (ValidationError, FoodNotFoundError, ValueError) as exc:
        return _back(q=q, error=str(exc))
    return _back(q=q, ok=f"Guardado: {food.name_es}")


@router.post("/admin/alimentos/retirar", response_model=None)
async def retirar(
    request: Request,
    session: Annotated[AsyncSession, Depends(db_session)],
    food_id: Annotated[str, Form()],
    q: Annotated[str, Form()] = "",
) -> RedirectResponse:
    repos = repos_of(request, session)
    try:
        await repos.foods.retire(UUID(food_id))
        await session.commit()
    except ValueError:
        return _back(q=q, error="Alimento inválido")
    return _back(q=q, ok="Retirado del catálogo. Los platos viejos lo siguen nombrando.")
