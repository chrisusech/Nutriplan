"""La membresía: cuántas semanas de menú tiene derecho a generar una cuenta.

El negocio en una frase: al registrarse regalamos UNA semana; para seguir, el
super_user le concede un mes (cuatro semanas que caducan). Cada concesión es una
fila que no se edita nunca, así que el saldo siempre se puede reconstruir y
explicar — quién dio qué, cuándo y por qué.

Ese formato es también el gancho para cobrar más adelante sin migrar nada: una
pasarela no hará otra cosa que insertar una concesión con `source=payment` y su
referencia de transacción. La regla de negocio no se entera.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Lo que regala el alta y lo que concede el super_user por defecto.
FREE_TRIAL_WEEKS = 1
FREE_TRIAL_DAYS = 14
DEFAULT_PLAN_WEEKS = 4
DEFAULT_PLAN_DAYS = 30
IAP_ANNUAL_WEEKS = 52
IAP_ANNUAL_DAYS = 365
IAP_MONTHLY_PRODUCT = "nutriplan.monthly"
IAP_ANNUAL_PRODUCT = "nutriplan.annual"
IAP_PRODUCTS: dict[str, tuple[int, int]] = {
    IAP_MONTHLY_PRODUCT: (DEFAULT_PLAN_WEEKS, DEFAULT_PLAN_DAYS),
    IAP_ANNUAL_PRODUCT: (IAP_ANNUAL_WEEKS, IAP_ANNUAL_DAYS),
}

# Regenerar la semana en curso no gasta otra semana, pero tampoco es gratis para
# nosotros: cada intento es una tanda de llamadas al proveedor.
MAX_REGENERATIONS_PER_WEEK = 2


class GrantSource(StrEnum):
    """De dónde salió la concesión."""

    SIGNUP_FREE = "signup_free"  # la semana de prueba del alta
    MANUAL = "manual"  # la activó el super_user
    PAYMENT = "payment"  # la pagó (StoreKit / Play / web)


class MembershipGrant(BaseModel):
    """Semanas concedidas a una cuenta. Append-only: la historia no se edita."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    weeks: int = Field(ge=1, le=52)
    granted_at: datetime
    # NULL = no caduca. La prueba y el mes sí caducan; un regalo puede no hacerlo.
    expires_at: datetime | None = None
    source: GrantSource = GrantSource.MANUAL
    granted_by: UUID | None = None
    external_ref: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=300)

    def is_live(self, now: datetime) -> bool:
        return self.expires_at is None or self.expires_at > now


class MembershipState(BaseModel):
    """El saldo de una cuenta, ya resuelto: lo que la UI y la puerta necesitan."""

    model_config = ConfigDict(frozen=True)

    weeks_granted: int  # vivas (no caducadas)
    weeks_used: int
    expires_at: datetime | None  # la caducidad más lejana de lo vivo
    has_expired_grants: bool  # había semanas y se le vencieron
    unlimited: bool = False  # el super_user: genera sin gastar nada

    @property
    def weeks_left(self) -> int:
        if self.unlimited:
            return 999
        return max(self.weeks_granted - self.weeks_used, 0)

    @property
    def can_generate(self) -> bool:
        return self.unlimited or self.weeks_left > 0

    @property
    def hint(self) -> str:
        """Qué decirle a la persona cuando la puerta está cerrada."""
        if self.can_generate:
            return ""
        if self.has_expired_grants:
            return "Tu plan venció. Actívalo otra vez para seguir generando tus semanas."
        return "Ya usaste las semanas que tenías. Activa tu plan mensual o anual para seguir."


def unlimited_membership() -> MembershipState:
    """El super_user no consume: la app es suya y la usa para probarla."""
    return MembershipState(
        weeks_granted=0,
        weeks_used=0,
        expires_at=None,
        has_expired_grants=False,
        unlimited=True,
    )


def consumption_window(grants: list[MembershipGrant], *, now: datetime) -> datetime | None:
    """Desde cuándo cuenta lo gastado: el arranque de la concesión viva más antigua.

    El saldo solo suma concesiones vivas, así que lo consumido tiene que medirse
    en esa misma ventana. Contarlo desde siempre hacía que el gasto de un plan
    ya vencido siguiera restando del que la persona acaba de pagar: quien usó su
    semana de prueba recibía tres del mes que compró, y al renovar el anual
    después de gastarlo entero se quedaba en cero.

    `None` = no hay nada vivo; entonces tampoco hay saldo del que restar.
    """
    starts = [g.granted_at for g in grants if g.is_live(now)]
    return min(starts) if starts else None


def evaluate_membership(
    grants: list[MembershipGrant], *, weeks_used: int, now: datetime
) -> MembershipState:
    """Saldo = semanas concedidas y no caducadas, menos las semanas ya generadas.

    Las caducadas se descuentan enteras: no se prorratea ni se devuelve nada.
    Que existan se recuerda igual, porque «se te venció» y «nunca tuviste» son
    dos conversaciones distintas con la persona.
    """
    live = [g for g in grants if g.is_live(now)]
    expiries = [g.expires_at for g in live if g.expires_at is not None]
    return MembershipState(
        weeks_granted=sum(g.weeks for g in live),
        weeks_used=max(weeks_used, 0),
        expires_at=max(expiries) if expiries else None,
        has_expired_grants=len(live) < len(grants),
    )


def can_regenerate_week(*, generations_this_week: int) -> bool:
    """Rehacer la semana en curso no gasta saldo, pero tiene tope."""
    return generations_this_week <= MAX_REGENERATIONS_PER_WEEK
