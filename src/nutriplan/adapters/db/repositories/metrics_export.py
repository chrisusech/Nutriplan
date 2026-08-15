"""Lo que la app sirvió: platos, recetas, semanas y uso.

Cruza tenants como `metrics.py` y por el mismo motivo: solo lo abre el
super_user. La diferencia es el detalle — aquí no se agrega para pintar una
barra, se saca la fila entera, porque la pregunta que se le va a hacer a estos
datos todavía no se sabe cuál es.

Su gemelo es `metrics_voices.py`, con lo que la gente escribió.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Float, case, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AppEventRow,
    ClientFoodBanRow,
    ClientFoodPreferenceRow,
    DishRatingRow,
    DishRecipeRow,
    FoodRow,
    MealEntryRow,
    PlanCycleRow,
)
from nutriplan.adapters.db.repositories._shared import not_lab, seudonimo


class SqlMetricsExportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def dishes(self) -> list[dict[str, Any]]:
        """Cada plantilla: cuántas veces se sirvió, cuántas se calificó y cómo.

        Las dos cifras juntas son el dato: un plato que sale mucho y casi nadie
        califica no es lo mismo que uno que sale poco y encanta.
        """
        veces = (
            await self._s.execute(
                select(MealEntryRow.template_id, func.count().label("veces"))
                .where(
                    MealEntryRow.template_id.is_not(None),
                    not_lab(MealEntryRow.tenant_id),
                )
                .group_by(MealEntryRow.template_id)
            )
        ).all()
        servidos: dict[str, int] = {r.template_id: r.veces for r in veces if r.template_id}
        stmt = (
            select(
                DishRatingRow.template_id,
                func.avg(cast(DishRatingRow.rating, Float)).label("media"),
                func.count().label("votos"),
                *(
                    func.sum(case((DishRatingRow.rating == n, 1), else_=0)).label(f"n{n}")
                    for n in range(1, 6)
                ),
            )
            .where(DishRatingRow.template_id.is_not(None), not_lab(DishRatingRow.tenant_id))
            .group_by(DishRatingRow.template_id)
        )
        notas = {r.template_id: r for r in (await self._s.execute(stmt)).all()}
        filas = [
            {
                "plantilla": plantilla,
                "veces_servido": servidos.get(plantilla, 0),
                "calificaciones": n.votos if n else 0,
                "nota_media": round(float(n.media), 2) if n else "",
                "cinco": n.n5 if n else 0,
                "cuatro": n.n4 if n else 0,
                "tres": n.n3 if n else 0,
                "dos": n.n2 if n else 0,
                "una": n.n1 if n else 0,
            }
            for plantilla, n in (
                (p, notas.get(p)) for p in sorted(set(servidos) | set(notas), key=str)
            )
        ]
        filas.sort(key=lambda d: (-int(d["veces_servido"]), str(d["plantilla"])))
        return filas

    async def recipes(self) -> list[dict[str, Any]]:
        """Las recetas que se han escrito y cómo les fue."""
        stmt = select(
            DishRecipeRow.name_es,
            DishRecipeRow.template_id,
            DishRecipeRow.times_served,
            DishRecipeRow.rating_avg,
            DishRecipeRow.rating_count,
            DishRecipeRow.source,
            DishRecipeRow.prep_minutes,
            DishRecipeRow.difficulty,
            DishRecipeRow.retired_at,
        ).order_by(desc(DishRecipeRow.times_served))
        return [
            {
                "receta": r.name_es,
                "plantilla": r.template_id or "",
                "veces_servida": r.times_served,
                "nota_media": round(float(r.rating_avg), 2) if r.rating_avg is not None else "",
                "votos": r.rating_count,
                "quien_la_escribio": "a mano" if r.source == "yaml" else "la IA",
                "minutos": r.prep_minutes if r.prep_minutes is not None else "",
                "dificultad": r.difficulty or "",
                "retirada": "sí" if r.retired_at else "no",
            }
            for r in (await self._s.execute(stmt)).all()
        ]

    async def chosen_foods(self) -> list[dict[str, Any]]:
        """Los alimentos que la gente marcó como suyos, y los que quitó."""
        filas: list[dict[str, Any]] = []
        for tabla, etiqueta in (
            (ClientFoodPreferenceRow, "Lo eligieron"),
            (ClientFoodBanRow, "Lo quitaron"),
        ):
            stmt = (
                select(FoodRow.name_es, FoodRow.category, func.count().label("personas"))
                .select_from(tabla)
                .join(FoodRow, FoodRow.id == tabla.food_id)
                .where(not_lab(tabla.tenant_id))
                .group_by(FoodRow.name_es, FoodRow.category)
                .order_by(desc(func.count()))
            )
            filas.extend(
                {
                    "alimento": r.name_es,
                    "grupo": r.category,
                    "que_paso": etiqueta,
                    "personas": r.personas,
                }
                for r in (await self._s.execute(stmt)).all()
            )
        return filas

    async def weeks(self) -> list[dict[str, Any]]:
        """Cada semana generada: cuándo, con qué motor y en qué versión."""
        stmt = (
            select(
                PlanCycleRow.week_start,
                PlanCycleRow.version,
                PlanCycleRow.status,
                PlanCycleRow.model,
                PlanCycleRow.config_version,
                PlanCycleRow.created_at,
                PlanCycleRow.client_id,
            )
            .where(not_lab(PlanCycleRow.tenant_id))
            .order_by(desc(PlanCycleRow.created_at))
        )
        return [
            {
                "semana": r.week_start,
                "generada_el": r.created_at.date(),
                "version": r.version,
                "estado": r.status,
                "motor": r.model,
                "config": r.config_version,
                "persona": seudonimo(r.client_id),
            }
            for r in (await self._s.execute(stmt)).all()
        ]

    async def usage(self, days: int = 90) -> list[dict[str, Any]]:
        """Qué pasó cada día en la app, en los últimos tres meses."""
        desde = datetime.now(UTC) - timedelta(days=days)
        dia = func.date(AppEventRow.at)
        stmt = (
            select(dia.label("dia"), AppEventRow.name, func.count().label("veces"))
            .where(AppEventRow.at >= desde, not_lab(AppEventRow.tenant_id))
            .group_by(dia, AppEventRow.name)
            .order_by(desc(dia), desc(func.count()))
        )
        return [
            {"dia": r.dia, "evento": r.name, "veces": r.veces}
            for r in (await self._s.execute(stmt)).all()
        ]
