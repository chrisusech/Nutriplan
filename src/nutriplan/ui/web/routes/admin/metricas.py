"""Las métricas del producto: dónde se cae la gente y qué platos funcionan."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import ClientRow
from nutriplan.application.admin_voices import CachedVoices, summarize_voices
from nutriplan.application.metrics_export import Datos, informe, to_csv
from nutriplan.ui.web.chart import barras
from nutriplan.ui.web.deps import container_of, db_session, render
from nutriplan.ui.web.metrics_view import dish_names, relabel, with_dish_name
from nutriplan.ui.web.presenter import (
    ACTIVITY_LABELS,
    EVENT_LABELS,
    FEEDBACK_LABELS,
    GOAL_LABELS,
    SEX_LABELS,
)

router = APIRouter()


def _voices_cache(request: Request) -> CachedVoices:
    cache = getattr(request.app.state, "voices_report", None)
    if not isinstance(cache, CachedVoices):
        cache = CachedVoices()
        request.app.state.voices_report = cache
    return cache


@router.get("/admin/metricas", response_class=HTMLResponse)
async def metrics(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> HTMLResponse:
    """Lo que el producto existe para averiguar.

    Los datos del laboratorio quedan fuera: el repositorio excluye los tenants
    del super_user, así que probar la app no mueve el embudo.
    """
    container = container_of(request)
    repo = container.metrics_repo(session)
    platos = dish_names(container.meal_catalog)
    pulse = await repo.pulse()
    return render(
        request,
        "admin_metricas.html",
        active_tab="metricas",
        pulse=pulse,
        grafica_dias=barras([float(n) for n in pulse.comidas_por_dia]),
        informe=_voices_cache(request).get(),
        funnel=await repo.funnel(),
        mejores=with_dish_name(await repo.dishes_by_rating(best=True), platos),
        peores=with_dish_name(await repo.dishes_by_rating(best=False), platos),
        objetivos=relabel(await repo.distribution(ClientRow.goal), valor=GOAL_LABELS),
        ciudades=await repo.distribution(ClientRow.city),
        habitos=await repo.eating_patterns(),
        comentarios=relabel(await repo.comments(), categoria=FEEDBACK_LABELS),
        notas=with_dish_name(await repo.rating_comments(), platos),
        eventos=relabel(await repo.events_last_days(), evento=EVENT_LABELS),
    )


@router.post("/admin/metricas/informe")
async def voices_report(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> RedirectResponse:
    """Un resumen de los comentarios. No se llama al recargar la página."""
    cache = _voices_cache(request)
    if cache.get() is None:
        container = container_of(request)
        repo = container.metrics_repo(session)
        voces = container.metrics_voices_repo(session)
        platos = dish_names(container.meal_catalog)
        report = await summarize_voices(
            closures=await voces.week_closures(),
            opinions=await repo.comments(),
            dish_notes=with_dish_name(await repo.rating_comments(), platos),
            llm=container.llm_client,
            prompts_dir=container.settings.prompts_dir,
            model=container.settings.llm_model_generate,
        )
        if report is not None:
            cache.put(report)
    return RedirectResponse("/admin/metricas", status_code=303)


@router.get("/admin/metricas.csv")
async def metrics_csv(
    request: Request, session: Annotated[AsyncSession, Depends(db_session)]
) -> Response:
    """Todo lo que el BETA ha dejado escrito, en un archivo.

    No es un resumen: es la materia prima para sentarse a mirar por qué un plato
    no gusta o qué se repite demasiado.
    """
    container = container_of(request)
    repo = container.metrics_repo(session)
    hechos = container.metrics_export_repo(session)
    voces = container.metrics_voices_repo(session)
    platos = dish_names(container.meal_catalog)

    embudo = await repo.funnel()
    datos = Datos(
        embudo=[{"paso": paso, "personas": n} for paso, n in embudo.pasos + embudo.aparte],
        platos=with_dish_name(await hechos.dishes(), platos),
        comentarios_de_platos=with_dish_name(await voces.dish_comments(), platos),
        cierres=await voces.week_closures(),
        peticiones=await voces.requests(),
        opiniones=relabel(await voces.opinions(), tipo=FEEDBACK_LABELS),
        perfiles=relabel(
            await voces.profiles(),
            objetivo=GOAL_LABELS,
            sexo=SEX_LABELS,
            actividad=ACTIVITY_LABELS,
        ),
        alimentos=await hechos.chosen_foods(),
        recetas=await hechos.recipes(),
        semanas=await hechos.weeks(),
        uso=relabel(await hechos.usage(), evento=EVENT_LABELS),
    )
    return Response(
        content=to_csv(informe(datos)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="nutriplan-analisis.csv"'},
    )
