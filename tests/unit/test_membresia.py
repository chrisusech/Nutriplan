"""La membresía: una semana de regalo, y después se paga.

El negocio entero cabe en estas historias. Se prueban con fechas explícitas
porque «se me venció» y «nunca lo tuve» son dos conversaciones distintas, y la
diferencia entre las dos es un `expires_at`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from nutriplan.application.membership import (
    can_generate_week,
    grant_free_trial,
    grant_weeks,
    membership_of,
    state_from_grants,
)
from nutriplan.domain.membership import (
    DEFAULT_PLAN_WEEKS,
    GrantSource,
    MembershipGrant,
    consumption_window,
    evaluate_membership,
)
from nutriplan.domain.models import Account, Role

AHORA = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
TENANT = uuid4()


def _cuenta(role: Role = Role.USER) -> Account:
    return Account(
        id=uuid4(),
        tenant_id=TENANT,
        email="ana@correo.com",
        name="Ana",
        password_hash="x",
        role=role,
    )


def _grant(
    weeks: int = 1, *, vence_en_dias: int | None = 14, user_id: UUID | None = None
) -> MembershipGrant:
    return MembershipGrant(
        id=uuid4(),
        tenant_id=TENANT,
        user_id=user_id or uuid4(),
        weeks=weeks,
        granted_at=AHORA,
        expires_at=AHORA + timedelta(days=vence_en_dias) if vence_en_dias else None,
    )


class _Membresias:
    """Repositorio en memoria: la membresía es aritmética, no SQL."""

    def __init__(self, grants: list[MembershipGrant] | None = None) -> None:
        self.grants = list(grants or [])

    async def add(self, grant: MembershipGrant) -> MembershipGrant:
        self.grants.append(grant)
        return grant

    async def list_for_user(self, user_id: UUID) -> list[MembershipGrant]:
        return [g for g in self.grants if g.user_id == user_id]

    async def has_source(self, user_id: UUID, source: GrantSource) -> bool:
        return any(g.user_id == user_id and g.source is source for g in self.grants)


class _Planes:
    """Los menús ya generados, con la fecha en la que se generaron.

    Guarda `(semana, cuándo)` porque lo que se gasta se mide en una ventana:
    lo generado bajo un plan que ya venció no puede restar del que se acaba de
    conceder.
    """

    def __init__(
        self, semanas: int = 0, *, generados: list[tuple[date, datetime]] | None = None
    ) -> None:
        if generados is not None:
            self.generados = list(generados)
        else:
            # Atajo para las historias a las que la fecha les da igual: todo
            # generado dentro de la ventana viva.
            self.generados = [
                (date(2026, 1, 5) + timedelta(weeks=i), AHORA) for i in range(semanas)
            ]

    async def count_weeks_since(self, client_id: UUID, since: datetime) -> int:
        return len({semana for semana, cuando in self.generados if cuando >= since})


class _Generaciones:
    """Los intentos de generar ESTA semana (los jobs, no los planes)."""

    def __init__(self, esta_semana: int = 0) -> None:
        self.esta_semana = esta_semana

    async def count_generations(self, client_id: UUID, week_start: date) -> int:
        return self.esta_semana


# --- El saldo ---------------------------------------------------------------


def test_quien_no_tiene_nada_concedido_no_puede_generar() -> None:
    estado = evaluate_membership([], weeks_used=0, now=AHORA)
    assert not estado.can_generate
    assert "Activa tu plan" in estado.hint


def test_el_saldo_en_lote_cuenta_solo_las_semanas_de_la_ventana() -> None:
    """El listado admin no puede ir a la base por cada fila."""
    grants = [_grant(weeks=4).model_copy(update={"granted_at": AHORA - timedelta(days=10)})]
    week_starts = [
        (AHORA - timedelta(days=40), date(2026, 6, 29)),
        (AHORA - timedelta(days=2), date(2026, 8, 10)),
    ]
    estado = state_from_grants(grants, week_starts=week_starts, now=AHORA)
    assert estado.weeks_used == 1
    assert estado.weeks_left == 3


def test_la_semana_de_regalo_alcanza_para_una_semana_y_no_para_dos() -> None:
    regalo = [_grant(weeks=1)]
    assert evaluate_membership(regalo, weeks_used=0, now=AHORA).weeks_left == 1
    assert not evaluate_membership(regalo, weeks_used=1, now=AHORA).can_generate


def test_un_plan_vencido_se_cuenta_como_vencido_no_como_inexistente() -> None:
    """Lo que se le dice a la persona depende de esta diferencia."""
    vencido = _grant(weeks=4, vence_en_dias=1)
    estado = evaluate_membership([vencido], weeks_used=0, now=AHORA + timedelta(days=2))
    assert estado.weeks_left == 0
    assert estado.has_expired_grants
    assert "venció" in estado.hint


def test_dos_concesiones_vivas_suman_sus_semanas() -> None:
    estado = evaluate_membership([_grant(weeks=1), _grant(weeks=4)], weeks_used=2, now=AHORA)
    assert estado.weeks_left == 3


def test_el_vencimiento_que_manda_es_el_mas_lejano() -> None:
    estado = evaluate_membership(
        [_grant(weeks=1, vence_en_dias=14), _grant(weeks=4, vence_en_dias=30)],
        weeks_used=0,
        now=AHORA,
    )
    assert estado.expires_at == AHORA + timedelta(days=30)


def test_generar_de_mas_nunca_deja_el_saldo_en_negativo() -> None:
    estado = evaluate_membership([_grant(weeks=1)], weeks_used=9, now=AHORA)
    assert estado.weeks_left == 0


# --- Conceder ---------------------------------------------------------------


async def test_al_registrarse_recibe_una_semana_de_prueba() -> None:
    cuenta, repo = _cuenta(), _Membresias()
    concedida = await grant_free_trial(account=cuenta, memberships=repo, now=AHORA)

    assert concedida is not None
    assert concedida.weeks == 1
    assert concedida.source is GrantSource.SIGNUP_FREE
    assert concedida.expires_at == AHORA + timedelta(days=14)


async def test_entrar_dos_veces_no_regala_dos_semanas() -> None:
    """El alta con Google pasa por aquí en cada login: no puede acumular."""
    cuenta, repo = _cuenta(), _Membresias()
    await grant_free_trial(account=cuenta, memberships=repo, now=AHORA)
    segunda = await grant_free_trial(account=cuenta, memberships=repo, now=AHORA)

    assert segunda is None
    assert len(repo.grants) == 1


async def test_el_super_user_no_gasta_semanas_de_prueba() -> None:
    repo = _Membresias()
    assert await grant_free_trial(account=_cuenta(Role.SUPER_USER), memberships=repo) is None


async def test_activarle_el_plan_le_da_cuatro_semanas_por_treinta_dias() -> None:
    cuenta, repo = _cuenta(), _Membresias()
    concedida = await grant_weeks(account=cuenta, memberships=repo, granted_by=uuid4(), now=AHORA)

    assert concedida.weeks == DEFAULT_PLAN_WEEKS
    assert concedida.expires_at == AHORA + timedelta(days=30)
    assert concedida.source is GrantSource.MANUAL


async def test_una_concesion_sin_caducidad_no_vence_nunca() -> None:
    cuenta, repo = _cuenta(), _Membresias()
    concedida = await grant_weeks(account=cuenta, memberships=repo, days_valid=None, now=AHORA)
    assert concedida.expires_at is None
    assert concedida.is_live(AHORA + timedelta(days=3650))


async def test_el_dia_que_haya_pasarela_el_pago_es_la_misma_concesion() -> None:
    """El hueco para cobrar ya existe: no hará falta migrar nada."""
    cuenta, repo = _cuenta(), _Membresias()
    concedida = await grant_weeks(
        account=cuenta,
        memberships=repo,
        source=GrantSource.PAYMENT,
        external_ref="ch_123",
        now=AHORA,
    )
    assert concedida.source is GrantSource.PAYMENT
    assert concedida.external_ref == "ch_123"


# --- La puerta --------------------------------------------------------------


async def test_con_saldo_puede_generar_la_semana() -> None:
    cuenta = _cuenta()
    repo = _Membresias([_grant(weeks=1, user_id=cuenta.id)])
    puede, motivo = await can_generate_week(
        account=cuenta,
        client_id=uuid4(),
        memberships=repo,
        plans=_Planes(semanas=0),
        generations=_Generaciones(),
        week_start=date(2026, 8, 10),
        now=AHORA,
    )
    assert puede and motivo == ""


async def test_sin_saldo_la_puerta_dice_exactamente_que_hacer() -> None:
    cuenta = _cuenta()
    repo = _Membresias([_grant(weeks=1, user_id=cuenta.id)])
    puede, motivo = await can_generate_week(
        account=cuenta,
        client_id=uuid4(),
        memberships=repo,
        plans=_Planes(semanas=1),
        generations=_Generaciones(),
        week_start=date(2026, 8, 10),
        now=AHORA,
    )
    assert not puede
    assert "Activa tu plan" in motivo


async def test_rehacer_la_semana_en_curso_no_gasta_otra_semana() -> None:
    """Ya pagó ESTA semana: pedirla de nuevo no puede cobrarle dos veces."""
    cuenta = _cuenta()
    repo = _Membresias([])  # sin saldo: solo la regeneración lo salva
    puede, _ = await can_generate_week(
        account=cuenta,
        client_id=uuid4(),
        memberships=repo,
        plans=_Planes(semanas=1),
        generations=_Generaciones(esta_semana=1),
        week_start=date(2026, 8, 10),
        now=AHORA,
    )
    assert puede


async def test_rehacerla_sin_parar_si_tiene_tope() -> None:
    cuenta = _cuenta()
    puede, motivo = await can_generate_week(
        account=cuenta,
        client_id=uuid4(),
        memberships=_Membresias(),
        plans=_Planes(semanas=1),
        generations=_Generaciones(esta_semana=3),
        week_start=date(2026, 8, 10),
        now=AHORA,
    )
    assert not puede
    assert "rehiciste" in motivo


async def test_el_super_user_genera_siempre_y_su_saldo_es_ilimitado() -> None:
    cuenta = _cuenta(Role.SUPER_USER)
    puede, _ = await can_generate_week(
        account=cuenta,
        client_id=uuid4(),
        memberships=_Membresias(),
        plans=_Planes(semanas=99),
        generations=_Generaciones(),
        now=AHORA,
    )
    estado = await membership_of(
        account=cuenta,
        client_id=uuid4(),
        memberships=_Membresias(),
        plans=_Planes(semanas=99),
        now=AHORA,
    )
    assert puede
    assert estado.unlimited and estado.can_generate


async def test_quien_todavia_no_tiene_perfil_no_ha_gastado_nada() -> None:
    cuenta = _cuenta()
    estado = await membership_of(
        account=cuenta,
        client_id=None,
        memberships=_Membresias([_grant(weeks=1, user_id=cuenta.id)]),
        plans=_Planes(semanas=7),
        now=AHORA,
    )
    assert estado.weeks_used == 0
    assert estado.can_generate


@pytest.mark.parametrize("semanas", [1, 4, 52])
def test_una_concesion_siempre_concede_al_menos_una_semana(semanas: int) -> None:
    assert _grant(weeks=semanas).weeks >= 1


# --- La ventana de consumo --------------------------------------------------
#
# Lo concedido solo cuenta si está vivo, así que lo gastado tiene que medirse en
# esa misma ventana. Estas tres historias son las que se rompían cuando el gasto
# se contaba desde siempre.


def test_sin_nada_vivo_no_hay_ventana_que_medir() -> None:
    vencido = _grant(weeks=4, vence_en_dias=1)
    assert consumption_window([vencido], now=AHORA + timedelta(days=2)) is None


def test_la_ventana_arranca_en_la_concesion_viva_mas_antigua() -> None:
    vieja = _grant(weeks=1, vence_en_dias=30)
    nueva = MembershipGrant(
        id=uuid4(),
        tenant_id=TENANT,
        user_id=vieja.user_id,
        weeks=4,
        granted_at=AHORA + timedelta(days=5),
        expires_at=AHORA + timedelta(days=40),
    )
    assert consumption_window([nueva, vieja], now=AHORA + timedelta(days=6)) == vieja.granted_at


async def test_quien_gasto_su_semana_de_prueba_recibe_completo_el_mes_que_compra() -> None:
    """La prueba venció con su semana gastada dentro. Eso no se arrastra."""
    cuenta = _cuenta()
    prueba = MembershipGrant(
        id=uuid4(),
        tenant_id=TENANT,
        user_id=cuenta.id,
        weeks=1,
        granted_at=AHORA,
        expires_at=AHORA + timedelta(days=14),
        source=GrantSource.SIGNUP_FREE,
    )
    compra = MembershipGrant(
        id=uuid4(),
        tenant_id=TENANT,
        user_id=cuenta.id,
        weeks=4,
        granted_at=AHORA + timedelta(days=20),
        expires_at=AHORA + timedelta(days=50),
        source=GrantSource.PAYMENT,
    )
    estado = await membership_of(
        account=cuenta,
        client_id=uuid4(),
        memberships=_Membresias([prueba, compra]),
        # La semana que generó con la prueba, generada antes de comprar.
        plans=_Planes(generados=[(date(2026, 8, 10), AHORA + timedelta(days=1))]),
        now=AHORA + timedelta(days=21),
    )
    assert estado.weeks_used == 0
    assert estado.weeks_left == 4


async def test_renovar_el_anual_devuelve_las_52_semanas_y_no_deja_a_nadie_fuera() -> None:
    """Gastó el anual entero. Renovarlo tiene que devolverle el año completo."""
    cuenta = _cuenta()
    viejo = MembershipGrant(
        id=uuid4(),
        tenant_id=TENANT,
        user_id=cuenta.id,
        weeks=52,
        granted_at=AHORA,
        expires_at=AHORA + timedelta(days=365),
        source=GrantSource.PAYMENT,
    )
    renovado = MembershipGrant(
        id=uuid4(),
        tenant_id=TENANT,
        user_id=cuenta.id,
        weeks=52,
        granted_at=AHORA + timedelta(days=366),
        expires_at=AHORA + timedelta(days=731),
        source=GrantSource.PAYMENT,
    )
    gastadas = [
        (date(2026, 1, 5) + timedelta(weeks=i), AHORA + timedelta(days=i * 7)) for i in range(52)
    ]
    estado = await membership_of(
        account=cuenta,
        client_id=uuid4(),
        memberships=_Membresias([viejo, renovado]),
        plans=_Planes(generados=gastadas),
        now=AHORA + timedelta(days=367),
    )
    assert estado.weeks_left == 52


async def test_dos_concesiones_vivas_solapadas_cuentan_el_consumo_desde_la_primera() -> None:
    """Mientras las dos sigan vivas, sus semanas suman y su gasto también."""
    cuenta = _cuenta()
    primera = _grant(weeks=1, vence_en_dias=60, user_id=cuenta.id)
    segunda = MembershipGrant(
        id=uuid4(),
        tenant_id=TENANT,
        user_id=cuenta.id,
        weeks=4,
        granted_at=AHORA + timedelta(days=3),
        expires_at=AHORA + timedelta(days=60),
    )
    estado = await membership_of(
        account=cuenta,
        client_id=uuid4(),
        memberships=_Membresias([primera, segunda]),
        plans=_Planes(
            generados=[
                (date(2026, 8, 10), AHORA + timedelta(days=1)),
                (date(2026, 8, 17), AHORA + timedelta(days=8)),
            ]
        ),
        now=AHORA + timedelta(days=10),
    )
    assert estado.weeks_granted == 5
    assert estado.weeks_used == 2
    assert estado.weeks_left == 3
