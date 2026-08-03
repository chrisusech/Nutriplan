"""Las consultas del BETA: qué come la gente y qué le gustó.

Cruza tenants a propósito — es el único sitio que lo hace, y solo devuelve
agregados. La ruta que lo usa exige `super_user` verificado contra la base.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AppEventRow,
    AppFeedbackRow,
    ClientRow,
    DishRatingRow,
    PlanCycleRow,
    UserRow,
)
from nutriplan.domain.models import Role

# Con menos calificaciones, una media no dice nada: un plato con un solo 5 no
# es "el mejor plato".
MIN_RATINGS = 3


@dataclass(frozen=True)
class Funnel:
    """Dónde se cae la gente."""

    registros: int
    consintieron: int
    completaron_onboarding: int
    generaron_menu: int
    calificaron: int
    opinaron: int

    @property
    def pasos(self) -> list[tuple[str, int]]:
        """La cadena de verdad: cada paso solo puede darlo quien dio el anterior."""
        return [
            ("Se registraron", self.registros),
            ("Completaron su perfil", self.completaron_onboarding),
            ("Generaron su menú", self.generaron_menu),
            ("Calificaron un plato", self.calificaron),
        ]

    @property
    def aparte(self) -> list[tuple[str, int]]:
        """No van en el embudo porque no dependen de los pasos previos:
        se puede escribir sin haber generado nada, y aceptar sin llegar lejos.
        Ponerlos en la barra los haría parecer una fuga que no existe."""
        return [
            ("Aceptaron compartir datos", self.consintieron),
            ("Nos escribieron", self.opinaron),
        ]


class SqlMetricsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def _count(self, stmt: Any) -> int:
        return int((await self._s.execute(stmt)).scalar() or 0)

    async def funnel(self) -> Funnel:
        # El super_user no es un usuario del BETA: contarlo inflaría el primer
        # paso y haría ver una fuga que no existe.
        activos = UserRow.deleted_at.is_(None) & (UserRow.role != Role.SUPER_USER.value)
        return Funnel(
            registros=await self._count(
                select(func.count()).select_from(UserRow).where(activos)
            ),
            consintieron=await self._count(
                select(func.count())
                .select_from(UserRow)
                .where(activos, UserRow.consent_analytics_at.is_not(None))
            ),
            completaron_onboarding=await self._count(
                select(func.count(func.distinct(ClientRow.user_id)))
            ),
            generaron_menu=await self._count(
                select(func.count(func.distinct(PlanCycleRow.tenant_id)))
            ),
            calificaron=await self._count(
                select(func.count(func.distinct(DishRatingRow.user_id)))
            ),
            opinaron=await self._count(
                select(func.count(func.distinct(AppFeedbackRow.user_id)))
            ),
        )

    async def dishes_by_rating(self, *, best: bool = True, limit: int = 8) -> list[dict[str, Any]]:
        """Los platos mejor y peor puntuados. El dato que justifica el BETA."""
        media = func.avg(DishRatingRow.rating)
        stmt = (
            select(
                DishRatingRow.template_id,
                media.label("media"),
                func.count().label("votos"),
            )
            .where(DishRatingRow.template_id.is_not(None))
            .group_by(DishRatingRow.template_id)
            .having(func.count() >= MIN_RATINGS)
            .order_by(desc(media) if best else media)
            .limit(limit)
        )
        rows = (await self._s.execute(stmt)).all()
        return [
            {"plantilla": r.template_id, "media": round(float(r.media), 2), "votos": r.votos}
            for r in rows
        ]

    async def distribution(self, column: Any, *, limit: int = 10) -> list[dict[str, Any]]:
        """Cuántas personas por objetivo, ciudad, sexo…"""
        stmt = (
            select(cast(column, String).label("valor"), func.count().label("total"))
            .where(column.is_not(None))
            .group_by(column)
            .order_by(desc(func.count()))
            .limit(limit)
        )
        return [
            {"valor": r.valor, "total": r.total} for r in (await self._s.execute(stmt)).all()
        ]

    async def eating_patterns(self, limit: int = 40) -> list[str]:
        """Lo que la gente contó sobre cómo come. El dato cualitativo: es la
        pregunta con la que arrancó todo esto."""
        stmt = (
            select(ClientRow.eating_pattern_raw)
            .where(ClientRow.eating_pattern_raw.is_not(None))
            .limit(limit)
        )
        return [r[0] for r in (await self._s.execute(stmt)).all() if r[0]]

    async def comments(self, limit: int = 40) -> list[dict[str, Any]]:
        stmt = (
            select(AppFeedbackRow.category, AppFeedbackRow.message,
                   AppFeedbackRow.nps, AppFeedbackRow.created_at)
            .order_by(desc(AppFeedbackRow.created_at))
            .limit(limit)
        )
        return [
            {"categoria": r.category, "mensaje": r.message, "nps": r.nps, "cuando": r.created_at}
            for r in (await self._s.execute(stmt)).all()
        ]

    async def rating_comments(self, limit: int = 30) -> list[dict[str, Any]]:
        stmt = (
            select(DishRatingRow.template_id, DishRatingRow.rating, DishRatingRow.comment)
            .where(DishRatingRow.comment.is_not(None))
            .order_by(desc(DishRatingRow.created_at))
            .limit(limit)
        )
        return [
            {"plantilla": r.template_id, "nota": r.rating, "comentario": r.comment}
            for r in (await self._s.execute(stmt)).all()
        ]

    async def events_last_days(self, days: int = 14) -> list[dict[str, Any]]:
        desde = datetime.now(UTC) - timedelta(days=days)
        stmt = (
            select(AppEventRow.name, func.count().label("total"))
            .where(AppEventRow.at >= desde)
            .group_by(AppEventRow.name)
            .order_by(desc(func.count()))
        )
        return [
            {"evento": r.name, "total": r.total} for r in (await self._s.execute(stmt)).all()
        ]
