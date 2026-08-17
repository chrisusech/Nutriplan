"""La puerta de la semana siguiente, resuelta en un solo sitio.

Tres pantallas preguntan lo mismo —«¿puede generar?»— y una de ellas además lo
tiene que impedir de verdad. Cuando la respuesta vivía en cada ruta, la
plantilla y el enforcement se contradecían y aparecía el botón que al pulsarlo
solo sabía decir que no.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from nutriplan.application.jobs import JobWeekGenerations
from nutriplan.application.membership import can_generate_week, membership_of
from nutriplan.application.weekly_checkin import week_closure
from nutriplan.domain.membership import MembershipState, unlimited_membership
from nutriplan.domain.models import Client, PlanCycle
from nutriplan.domain.week import plan_is_live, today_bogota
from nutriplan.domain.week_close import WeekClosure
from nutriplan.ui.web.deps import acting_account, container_of, repos_of

WAITING_NEXT_WEEK = "Al terminar tu semana preparamos el siguiente menú."
PLAN_LOCKED = "Tu semana ya terminó. Activa tu plan para cambiar platos."


@dataclass(frozen=True)
class WeekGate:
    """Si puede generar la semana siguiente, y qué le falta si no."""

    membership: MembershipState
    closure: WeekClosure
    allowed: bool
    reason: str

    @property
    def needs_checkin(self) -> bool:
        return not self.closure.is_closed

    @property
    def waiting_next_week(self) -> bool:
        return self.reason == WAITING_NEXT_WEEK


def plan_mutable(plan: PlanCycle | None, today: date | None = None) -> bool:
    """Cambiar plato o restaurante: solo mientras la tira de 7 días sigue viva."""
    return plan is not None and plan_is_live(plan.week_start, today)


async def week_gate(
    request: Request,
    session: AsyncSession,
    client: Client,
    *,
    today: date | None = None,
    plan: PlanCycle | None = None,
) -> WeekGate:
    """El estado de la puerta: cierre de semana primero, saldo después.

    Ese orden importa: a quien le falta cerrar la semana hay que pedirle que la
    cierre, no venderle un plan que aún no necesita.
    """
    day = today or today_bogota()
    repos = repos_of(request, session)
    account = await acting_account(request, session)
    if account is None:
        return WeekGate(
            membership=unlimited_membership(),
            closure=WeekClosure(
                has_weight=True,
                ratings=0,
                ratings_required=0,
                has_comment=True,
                is_first_week=True,
            ),
            allowed=False,
            reason="Vuelve a entrar para generar tu semana.",
        )

    if plan is None and client.active_plan_id is not None:
        plan = await repos.plans.get(client.active_plan_id)
    meals = sum(len(d.meals) for d in plan.days) if plan is not None else 0
    closure = await week_closure(
        client=client,
        weights=repos.weights,
        ratings=repos.ratings,
        meals_in_plan=meals,
        today=day,
        week_start=plan.week_start if plan is not None else None,
    )
    membership = await membership_of(
        account=account,
        client_id=client.id,
        memberships=container_of(request).membership_repo(session),
        plans=repos.plans,
    )
    if not membership.unlimited and not closure.is_closed:
        return WeekGate(membership=membership, closure=closure, allowed=False, reason=closure.hint)

    # Quien ya vive ESTA tira no regenera a mano: al terminar se arma la siguiente.
    # Sin saldo esa frase miente: el tick no le genera nada.
    live = plan is not None and plan_is_live(plan.week_start, day)
    if membership.can_generate and not membership.unlimited and live:
        return WeekGate(
            membership=membership,
            closure=closure,
            allowed=False,
            reason=WAITING_NEXT_WEEK,
        )

    target = plan.week_start if live and plan is not None else day
    allowed, reason = await can_generate_week(
        account=account,
        client_id=client.id,
        memberships=container_of(request).membership_repo(session),
        plans=repos.plans,
        generations=JobWeekGenerations(repos.jobs),
        week_start=target,
    )
    return WeekGate(membership=membership, closure=closure, allowed=allowed, reason=reason)
