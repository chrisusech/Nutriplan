"""La caché del guard de sesión: rápida, pero nunca por encima de la verdad."""

from uuid import uuid4

from nutriplan.domain.models import Account, Role
from nutriplan.ui.web.account_cache import AccountGateCache


def _cuenta(activa: bool = True) -> Account:
    return Account(
        id=uuid4(),
        tenant_id=uuid4(),
        email="ana@correo.com",
        name="Ana",
        role=Role.USER,
        is_active=activa,
    )


def test_la_segunda_peticion_seguida_no_vuelve_a_preguntar() -> None:
    """Es lo que evita dos conexiones nuevas por cada poll de la pantalla."""
    cache = AccountGateCache()
    cuenta = _cuenta()
    cache.put(cuenta.email, cuenta)
    assert cache.get(cuenta.email) is cuenta


def test_pasados_los_segundos_se_vuelve_a_preguntar() -> None:
    cache = AccountGateCache(ttl_s=0)
    cuenta = _cuenta()
    cache.put(cuenta.email, cuenta)
    assert cache.get(cuenta.email) is None


def test_desactivar_una_cuenta_surte_efecto_en_el_acto() -> None:
    """Sin esto, alguien a quien acaban de desactivar seguiría dentro."""
    cache = AccountGateCache()
    cuenta = _cuenta()
    cache.put(cuenta.email, cuenta)

    cache.drop(cuenta.email)
    assert cache.get(cuenta.email) is None


def test_a_quien_nunca_entro_no_se_le_inventa_una_cuenta() -> None:
    assert AccountGateCache().get("desconocida@correo.com") is None
