"""Las consultas del BETA: qué come la gente y qué le gustó.

Cruza tenants a propósito — es el único sitio que lo hace, y solo devuelve
agregados. La ruta que lo usa exige `super_user` verificado contra la base.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import String, cast, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.adapters.db.models import (
    AppEventRow,
    AppFeedbackRow,
    ClientRow,
    DayPlanRow,
    DishRatingRow,
    GenerationJobRow,
    MealEntryRow,
    MembershipGrantRow,
    PlanCycleRow,
    UserRow,
)
from nutriplan.adapters.db.repositories._shared import not_lab
from nutriplan.domain.models import Role
from nutriplan.domain.week import iso_week_start
from nutriplan.ports.job_repository import JobStatus

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


@dataclass(frozen=True)
class ProductPulse:
    """Los números de la consola: gente viva, platos, quejas."""

    activos: int
    con_menu: int
    comidas: int
    comidas_marcadas: int
    dias_completos: int
    fallas: int
    jobs_fallidos: int
    detractores: int
    comidas_por_dia: list[int]

    @property
    def cumplimiento(self) -> int:
        if self.comidas <= 0:
            return 0
        return round(100 * self.comidas_marcadas / self.comidas)


class SqlMetricsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    def _not_lab(self, column: Any) -> Any:
        return not_lab(column)

    async def _count(self, stmt: Any) -> int:
        return int((await self._s.execute(stmt)).scalar() or 0)

    async def funnel(self) -> Funnel:
        # El super_user no es un usuario del BETA: contarlo inflaría el primer
        # paso y haría ver una fuga que no existe.
        activos = UserRow.deleted_at.is_(None) & (UserRow.role != Role.SUPER_USER.value)
        return Funnel(
            registros=await self._count(select(func.count()).select_from(UserRow).where(activos)),
            consintieron=await self._count(
                select(func.count())
                .select_from(UserRow)
                .where(activos, UserRow.consent_analytics_at.is_not(None))
            ),
            completaron_onboarding=await self._count(
                select(func.count(func.distinct(ClientRow.user_id))).where(
                    self._not_lab(ClientRow.tenant_id)
                )
            ),
            generaron_menu=await self._count(
                select(func.count(func.distinct(PlanCycleRow.tenant_id))).where(
                    self._not_lab(PlanCycleRow.tenant_id)
                )
            ),
            calificaron=await self._count(
                select(func.count(func.distinct(DishRatingRow.user_id))).where(
                    self._not_lab(DishRatingRow.tenant_id)
                )
            ),
            opinaron=await self._count(
                select(func.count(func.distinct(AppFeedbackRow.user_id))).where(
                    self._not_lab(AppFeedbackRow.tenant_id)
                )
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
            .where(
                DishRatingRow.template_id.is_not(None),
                self._not_lab(DishRatingRow.tenant_id),
            )
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
        return [{"valor": r.valor, "total": r.total} for r in (await self._s.execute(stmt)).all()]

    async def eating_patterns(self, limit: int = 40) -> list[str]:
        """Lo que la gente contó sobre cómo come. El dato cualitativo: es la
        pregunta con la que arrancó todo esto."""
        stmt = (
            select(ClientRow.eating_pattern_raw)
            .where(
                ClientRow.eating_pattern_raw.is_not(None),
                self._not_lab(ClientRow.tenant_id),
            )
            .limit(limit)
        )
        return [r[0] for r in (await self._s.execute(stmt)).all() if r[0]]

    async def comments(self, limit: int = 40) -> list[dict[str, Any]]:
        stmt = (
            select(
                AppFeedbackRow.category,
                AppFeedbackRow.message,
                AppFeedbackRow.nps,
                AppFeedbackRow.created_at,
            )
            .where(self._not_lab(AppFeedbackRow.tenant_id))
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
            .where(
                DishRatingRow.comment.is_not(None),
                self._not_lab(DishRatingRow.tenant_id),
            )
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
        return [{"evento": r.name, "total": r.total} for r in (await self._s.execute(stmt)).all()]

    async def pulse(self, *, week_start: date | None = None, days: int = 30) -> ProductPulse:
        """Los cuatro números de la home: sin filas por persona."""
        week = week_start or iso_week_start()
        desde = datetime.now(UTC) - timedelta(days=days)
        comidas, marcadas, completos, por_dia = await self._week_meals(week)
        return ProductPulse(
            activos=await self._active_members(),
            con_menu=await self._count(
                select(func.count(func.distinct(PlanCycleRow.client_id))).where(
                    PlanCycleRow.week_start == week,
                    self._not_lab(PlanCycleRow.tenant_id),
                )
            ),
            comidas=comidas,
            comidas_marcadas=marcadas,
            dias_completos=completos,
            fallas=await self._count(
                select(func.count(func.distinct(AppFeedbackRow.user_id))).where(
                    AppFeedbackRow.category == "bug",
                    AppFeedbackRow.created_at >= desde,
                    self._not_lab(AppFeedbackRow.tenant_id),
                )
            ),
            jobs_fallidos=await self._count(
                select(func.count())
                .select_from(GenerationJobRow)
                .where(
                    GenerationJobRow.status == JobStatus.FAILED.value,
                    GenerationJobRow.created_at >= desde,
                    self._not_lab(GenerationJobRow.tenant_id),
                )
            ),
            detractores=await self._count(
                select(func.count(func.distinct(AppFeedbackRow.user_id))).where(
                    AppFeedbackRow.nps.is_not(None),
                    AppFeedbackRow.nps <= 6,
                    AppFeedbackRow.created_at >= desde,
                    self._not_lab(AppFeedbackRow.tenant_id),
                )
            ),
            comidas_por_dia=por_dia,
        )

    async def _active_members(self) -> int:
        """Cuentas con saldo: semanas vivas menos las ya generadas."""
        now = datetime.now(UTC)
        live = (
            select(
                MembershipGrantRow.user_id.label("uid"),
                func.sum(MembershipGrantRow.weeks).label("granted"),
                func.min(MembershipGrantRow.granted_at).label("since"),
            )
            .join(UserRow, UserRow.id == MembershipGrantRow.user_id)
            .where(
                UserRow.role != Role.SUPER_USER.value,
                UserRow.deleted_at.is_(None),
                or_(
                    MembershipGrantRow.expires_at.is_(None),
                    MembershipGrantRow.expires_at > now,
                ),
            )
            .group_by(MembershipGrantRow.user_id)
            .subquery()
        )
        used = (
            select(
                ClientRow.user_id.label("uid"),
                func.count(func.distinct(PlanCycleRow.week_start)).label("used"),
            )
            .join(PlanCycleRow, PlanCycleRow.client_id == ClientRow.id)
            .join(live, live.c.uid == ClientRow.user_id)
            .where(PlanCycleRow.created_at >= live.c.since)
            .group_by(ClientRow.user_id)
            .subquery()
        )
        return await self._count(
            select(func.count())
            .select_from(live)
            .outerjoin(used, used.c.uid == live.c.uid)
            .where(live.c.granted > func.coalesce(used.c.used, 0))
        )

    async def _week_meals(self, week: date) -> tuple[int, int, int, list[int]]:
        """Una pasada: totales, marcadas, días cerrados y barras por día."""
        rows = (
            await self._s.execute(
                select(DayPlanRow.id, DayPlanRow.day_index, MealEntryRow.eaten)
                .join(MealEntryRow, MealEntryRow.day_plan_id == DayPlanRow.id)
                .join(PlanCycleRow, PlanCycleRow.id == DayPlanRow.plan_cycle_id)
                .where(
                    PlanCycleRow.week_start == week,
                    self._not_lab(PlanCycleRow.tenant_id),
                )
            )
        ).all()
        por_dia_n: dict[int, list[bool]] = {}
        por_dow = [0] * 7
        for day_id, day_index, eaten in rows:
            por_dia_n.setdefault(int(day_id), []).append(bool(eaten))
            if bool(eaten) and 0 <= int(day_index) <= 6:
                por_dow[int(day_index)] += 1
        comidas = sum(len(v) for v in por_dia_n.values())
        marcadas = sum(sum(1 for e in v if e) for v in por_dia_n.values())
        completos = sum(1 for v in por_dia_n.values() if v and all(v))
        return comidas, marcadas, completos, por_dow
