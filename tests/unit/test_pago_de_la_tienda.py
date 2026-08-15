"""Una compra de la tienda concede semanas; la misma transacción no se cobra dos veces."""

from datetime import UTC, datetime
from uuid import uuid4

from nutriplan.application.membership import grant_iap
from nutriplan.domain.membership import (
    IAP_ANNUAL_PRODUCT,
    IAP_MONTHLY_PRODUCT,
    GrantSource,
    MembershipGrant,
)
from nutriplan.domain.models import Account, Role

AHORA = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
TENANT = uuid4()


def _cuenta() -> Account:
    return Account(
        id=uuid4(),
        tenant_id=TENANT,
        email="ana@correo.com",
        name="Ana",
        password_hash="x",
        role=Role.USER,
    )


class _Membresias:
    def __init__(self) -> None:
        self.grants: list[MembershipGrant] = []

    async def add(self, grant: MembershipGrant) -> MembershipGrant:
        self.grants.append(grant)
        return grant

    async def list_for_user(self, user_id):  # noqa: ANN001
        return [g for g in self.grants if g.user_id == user_id]

    async def has_source(self, user_id, source):  # noqa: ANN001
        return any(g.user_id == user_id and g.source is source for g in self.grants)

    async def find_by_external_ref(self, external_ref: str) -> MembershipGrant | None:
        return next((g for g in self.grants if g.external_ref == external_ref), None)


async def test_una_compra_mensual_regala_cuatro_semanas() -> None:
    cuenta = _cuenta()
    repo = _Membresias()
    grant = await grant_iap(
        account=cuenta,
        memberships=repo,
        product_id=IAP_MONTHLY_PRODUCT,
        transaction_id="txn-apple-1",
        now=AHORA,
    )
    assert grant.source is GrantSource.PAYMENT
    assert grant.weeks == 4
    assert grant.external_ref == "txn-apple-1"


async def test_la_misma_transaccion_no_concede_dos_veces() -> None:
    cuenta = _cuenta()
    repo = _Membresias()
    a = await grant_iap(
        account=cuenta,
        memberships=repo,
        product_id=IAP_ANNUAL_PRODUCT,
        transaction_id="txn-apple-2",
        now=AHORA,
    )
    b = await grant_iap(
        account=cuenta,
        memberships=repo,
        product_id=IAP_ANNUAL_PRODUCT,
        transaction_id="txn-apple-2",
        now=AHORA,
    )
    assert a.id == b.id
    assert len(repo.grants) == 1
    assert a.weeks == 52


async def test_sin_transaccion_no_se_regalan_semanas() -> None:
    import pytest

    with pytest.raises(ValueError, match="transacción"):
        await grant_iap(
            account=_cuenta(),
            memberships=_Membresias(),
            product_id=IAP_MONTHLY_PRODUCT,
            transaction_id="  ",
        )


async def test_un_producto_inventado_no_concede() -> None:
    import pytest

    with pytest.raises(ValueError, match="desconocido"):
        await grant_iap(
            account=_cuenta(),
            memberships=_Membresias(),
            product_id="nutriplan.lifetime",
            transaction_id="txn-x",
        )
