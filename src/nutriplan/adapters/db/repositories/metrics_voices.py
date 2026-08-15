"""Lo que la gente escribió: comentarios, cierres, peticiones y opiniones.

El gemelo cualitativo de `metrics_export.py`, y la mitad que de verdad explica
las cifras: una nota de 2 no dice nada hasta que se lee el «me repitió mucho».

No sale nada que identifique a nadie — ni nombre, ni correo, ni id de cuenta.
Una queja sobre el brócoli sirve igual sin saber quién la escribió.
"""

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AppFeedbackRow,
    ClientRow,
    ClientTasteSignalRow,
    DishRatingRow,
    FoodRow,
    WeightEntryRow,
)
from nutriplan.adapters.db.repositories._shared import not_lab, seudonimo


class SqlMetricsVoicesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def dish_comments(self) -> list[dict[str, Any]]:
        """Lo que escribieron al calificar: el «por qué» de cada nota."""
        stmt = (
            select(
                DishRatingRow.created_at,
                DishRatingRow.template_id,
                DishRatingRow.slot,
                DishRatingRow.rating,
                DishRatingRow.comment,
            )
            .where(
                DishRatingRow.comment.is_not(None),
                DishRatingRow.comment != "",
                not_lab(DishRatingRow.tenant_id),
            )
            .order_by(desc(DishRatingRow.created_at))
        )
        return [
            {
                "fecha": r.created_at.date(),
                "plantilla": r.template_id,
                "comida": r.slot,
                "nota": r.rating,
                "comentario": r.comment,
            }
            for r in (await self._s.execute(stmt)).all()
        ]

    async def week_closures(self) -> list[dict[str, Any]]:
        """El pesaje de cada semana y lo que contaron al cerrarla."""
        stmt = (
            select(
                WeightEntryRow.week_start,
                WeightEntryRow.weight_kg,
                WeightEntryRow.client_comment,
                WeightEntryRow.client_id,
            )
            .where(not_lab(WeightEntryRow.tenant_id))
            .order_by(desc(WeightEntryRow.week_start))
        )
        return [
            {
                "semana": r.week_start,
                "peso_kg": r.weight_kg,
                "comentario": r.client_comment or "",
                "persona": seudonimo(r.client_id),
            }
            for r in (await self._s.execute(stmt)).all()
        ]

    async def requests(self) -> list[dict[str, Any]]:
        """Lo que se pidió cambiar del menú, semana a semana.

        De aquí salen las quejas de repetición y los «esto no lo quiero más»:
        son las señales con las que se arma la semana siguiente.
        """
        nombres = await self._food_names()
        stmt = (
            select(
                ClientTasteSignalRow.week_start,
                ClientTasteSignalRow.avoid_food_ids,
                ClientTasteSignalRow.prefer_food_ids,
                ClientTasteSignalRow.adjustments,
                ClientTasteSignalRow.source,
                ClientTasteSignalRow.client_id,
            )
            .where(not_lab(ClientTasteSignalRow.tenant_id))
            .order_by(desc(ClientTasteSignalRow.week_start))
        )
        filas: list[dict[str, Any]] = []
        for r in (await self._s.execute(stmt)).all():
            por_tipo = (
                ("No quiere volver a verlo", [nombres.get(f, f) for f in r.avoid_food_ids]),
                ("Quiere más", [nombres.get(f, f) for f in r.prefer_food_ids]),
                ("Lo pidió con sus palabras", list(r.adjustments)),
            )
            filas.extend(
                {
                    "semana": r.week_start,
                    "tipo": tipo,
                    "detalle": detalle,
                    "lo_dedujo": "la IA" if r.source == "ai" else "las reglas",
                    "persona": seudonimo(r.client_id),
                }
                for tipo, valores in por_tipo
                for detalle in valores
            )
        return filas

    async def opinions(self) -> list[dict[str, Any]]:
        """Lo que nos escriben desde «Opinar»."""
        stmt = (
            select(
                AppFeedbackRow.created_at,
                AppFeedbackRow.category,
                AppFeedbackRow.nps,
                AppFeedbackRow.platform,
                AppFeedbackRow.message,
            )
            .where(not_lab(AppFeedbackRow.tenant_id))
            .order_by(desc(AppFeedbackRow.created_at))
        )
        return [
            {
                "fecha": r.created_at.date(),
                "tipo": r.category,
                "nps": r.nps if r.nps is not None else "",
                "desde": r.platform or "web",
                "mensaje": r.message,
            }
            for r in (await self._s.execute(stmt)).all()
        ]

    async def profiles(self) -> list[dict[str, Any]]:
        """Quién usa esto y cómo dijo que come."""
        stmt = (
            select(
                ClientRow.sex,
                ClientRow.age_years,
                ClientRow.goal,
                ClientRow.activity_level,
                ClientRow.city,
                ClientRow.meal_slots,
                ClientRow.restrictions,
                ClientRow.dislikes,
                ClientRow.context_tags,
                ClientRow.eating_pattern_raw,
            )
            .where(not_lab(ClientRow.tenant_id))
            .order_by(ClientRow.goal)
        )
        return [
            {
                "sexo": r.sex,
                "edad": r.age_years,
                "objetivo": r.goal,
                "actividad": r.activity_level,
                "ciudad": r.city or "",
                "comidas_al_dia": len(r.meal_slots) if r.meal_slots else 5,
                "restricciones": " · ".join(r.restrictions),
                "no_le_gusta": " · ".join(r.dislikes),
                "contexto": " · ".join(r.context_tags),
                "como_come": r.eating_pattern_raw or "",
            }
            for r in (await self._s.execute(stmt)).all()
        ]

    async def _food_names(self) -> dict[str, str]:
        rows = (await self._s.execute(select(FoodRow.id, FoodRow.name_es))).all()
        return {str(r.id): r.name_es for r in rows}
