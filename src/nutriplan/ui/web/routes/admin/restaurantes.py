"""Catálogo de restaurantes: marcas y platos de calle, en YAML.

No hay tabla SQL. Añadir una marca o un plato escribe el archivo y recarga el
catálogo, igual que promover una receta.
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from nutriplan.adapters.meals.restaurant_store import (
    RestaurantCatalogError,
    append_dish,
    append_restaurant,
)
from nutriplan.domain.restaurant import Restaurant, RestaurantDish
from nutriplan.ui.web.deps import container_of, render

router = APIRouter()


@router.get("/admin/restaurantes", response_class=HTMLResponse)
async def restaurants_page(request: Request, error: str = "", ok: str = "") -> HTMLResponse:
    catalog = container_of(request).restaurant_catalog
    return render(
        request,
        "admin_restaurantes.html",
        active_tab="restaurantes",
        catalogo=catalog,
        error=error,
        ok=ok,
    )


@router.post("/admin/restaurantes", response_model=None)
async def add_restaurant(
    request: Request,
    id: Annotated[str, Form()],
    name: Annotated[str, Form()],
) -> RedirectResponse:
    rid = id.strip().lower()
    try:
        marca = Restaurant(id=rid, name=name.strip(), dishes=[])
    except ValidationError as exc:
        return _back(error=_primer_error(exc))

    try:
        added = append_restaurant(container_of(request).settings.restaurants_catalog_path, marca)
    except RestaurantCatalogError as exc:
        return _back(error=str(exc))

    container_of(request).reload_restaurant_catalog()
    return _back(
        ok=f"«{marca.name}» ya está en el catálogo." if added else "Ese restaurante ya estaba."
    )


@router.post("/admin/restaurantes/{restaurant_id}/platos", response_model=None)
async def add_dish(
    request: Request,
    restaurant_id: str,
    id: Annotated[str, Form()],
    name: Annotated[str, Form()],
    portion: Annotated[str, Form()] = "1 porción",
    protein_g: Annotated[float, Form()] = 0.0,
    carb_g: Annotated[float, Form()] = 0.0,
    fat_g: Annotated[float, Form()] = 0.0,
) -> RedirectResponse:
    try:
        plato = RestaurantDish(
            id=id.strip().lower(),
            name=name.strip(),
            portion=portion.strip() or "1 porción",
            protein_g=protein_g,
            carb_g=carb_g,
            fat_g=fat_g,
        )
    except ValidationError as exc:
        return _back(error=_primer_error(exc))

    try:
        added = append_dish(
            container_of(request).settings.restaurants_catalog_path,
            restaurant_id,
            plato,
        )
    except RestaurantCatalogError as exc:
        return _back(error=str(exc))

    container_of(request).reload_restaurant_catalog()
    return _back(
        ok=f"«{plato.name}» ya está en el catálogo."
        if added
        else "Ese plato ya estaba en esa marca."
    )


def _primer_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
    msg = str(err.get("msg", "dato inválido"))
    return f"{loc}: {msg}" if loc else msg


def _back(*, error: str = "", ok: str = "") -> RedirectResponse:
    if error:
        return RedirectResponse(f"/admin/restaurantes?error={quote(error)}", status_code=303)
    if ok:
        return RedirectResponse(f"/admin/restaurantes?ok={quote(ok)}", status_code=303)
    return RedirectResponse("/admin/restaurantes", status_code=303)
