"""Una compra de App Store solo concede semanas si Apple firmó el JWS."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nutriplan.adapters.iap.fake import FakeStoreVerifier
from nutriplan.application.iap import activate_verified_purchase, apply_store_notification
from nutriplan.application.membership import grant_iap
from nutriplan.domain.membership import (
    APP_BUNDLE_ID,
    IAP_ANNUAL_PRODUCT,
    IAP_MONTHLY_PRODUCT,
    GrantSource,
    MembershipGrant,
    evaluate_membership,
)
from nutriplan.domain.models import Account, Role
from nutriplan.ports.iap import IAPError, StoreNotification, VerifiedPurchase

AHORA = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
TENANT = uuid4()
SANDBOX_JWS = "sandbox.jws.firmado"


def _cuenta() -> Account:
    return Account(
        id=uuid4(),
        tenant_id=TENANT,
        email="ana@correo.com",
        name="Ana",
        password_hash="x",
        role=Role.USER,
    )


def _compra(
    *,
    txn: str = "1001",
    original: str = "1000",
    product: str = IAP_MONTHLY_PRODUCT,
    bundle: str = APP_BUNDLE_ID,
    environment: str = "Sandbox",
) -> VerifiedPurchase:
    return VerifiedPurchase(
        transaction_id=txn,
        original_transaction_id=original,
        product_id=product,
        bundle_id=bundle,
        environment=environment,
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

    async def find_user_by_original_transaction(self, original_transaction_id: str):
        found = next(
            (g for g in self.grants if g.original_transaction_id == original_transaction_id),
            None,
        )
        return None if found is None else found.user_id

    async def expire_by_original_transaction(
        self, original_transaction_id: str, *, now: datetime
    ) -> int:
        n = 0
        updated: list[MembershipGrant] = []
        for grant in self.grants:
            if grant.original_transaction_id == original_transaction_id and grant.is_live(now):
                updated.append(grant.model_copy(update={"expires_at": now}))
                n += 1
            else:
                updated.append(grant)
        self.grants = updated
        return n


class _Cuentas:
    def __init__(self, account: Account) -> None:
        self.account = account

    async def get_by_id(self, user_id):  # noqa: ANN001
        return self.account if user_id == self.account.id else None


async def test_un_jws_de_sandbox_concede_en_prod() -> None:
    """El revisor cobra en Sandbox contra el binario de prod."""
    cuenta = _cuenta()
    repo = _Membresias()
    purchase = _compra()
    verifier = FakeStoreVerifier(purchases={SANDBOX_JWS: purchase})
    found = verifier.verify_transaction(SANDBOX_JWS)
    grant = await activate_verified_purchase(
        account=cuenta, memberships=repo, purchase=found, now=AHORA
    )
    assert grant.source is GrantSource.PAYMENT
    assert grant.weeks == 4
    assert grant.external_ref == "1001"
    assert grant.original_transaction_id == "1000"
    assert found.environment == "Sandbox"


async def test_un_jws_con_bundle_ajeno_no_concede() -> None:
    cuenta = _cuenta()
    repo = _Membresias()
    purchase = _compra(bundle="com.otra.app")
    with pytest.raises(IAPError, match="esta aplicación"):
        await activate_verified_purchase(
            account=cuenta, memberships=repo, purchase=purchase, now=AHORA
        )
    assert repo.grants == []


async def test_un_id_inventado_no_abre_semanas() -> None:
    with pytest.raises(IAPError, match="no es válida"):
        FakeStoreVerifier().verify_transaction("txn-inventado")


async def test_la_misma_transaccion_no_duplica_semanas() -> None:
    cuenta = _cuenta()
    repo = _Membresias()
    purchase = _compra(txn="txn-unica", original="txn-unica")
    a = await activate_verified_purchase(
        account=cuenta, memberships=repo, purchase=purchase, now=AHORA
    )
    b = await activate_verified_purchase(
        account=cuenta, memberships=repo, purchase=purchase, now=AHORA
    )
    assert a.id == b.id
    assert len(repo.grants) == 1


async def test_did_renew_concede_otras_cuatro_semanas() -> None:
    cuenta = _cuenta()
    repo = _Membresias()
    primera = _compra(txn="1001", original="1000")
    await activate_verified_purchase(account=cuenta, memberships=repo, purchase=primera, now=AHORA)
    renovacion = _compra(txn="1002", original="1000")
    status = await apply_store_notification(
        notification=StoreNotification(notification_type="DID_RENEW", purchase=renovacion),
        memberships=repo,
        accounts=_Cuentas(cuenta),
        now=AHORA + timedelta(days=30),
    )
    assert status == "renewed"
    assert len(repo.grants) == 2
    assert {g.external_ref for g in repo.grants} == {"1001", "1002"}


async def test_un_reembolso_caduca_las_concesiones_de_esa_suscripcion() -> None:
    cuenta = _cuenta()
    repo = _Membresias()
    purchase = _compra(txn="1001", original="1000")
    await activate_verified_purchase(account=cuenta, memberships=repo, purchase=purchase, now=AHORA)
    later = AHORA + timedelta(days=2)
    status = await apply_store_notification(
        notification=StoreNotification(notification_type="REFUND", purchase=purchase),
        memberships=repo,
        accounts=_Cuentas(cuenta),
        now=later,
    )
    assert status == "expired"
    state = evaluate_membership(repo.grants, weeks_used=0, now=later)
    assert state.weeks_left == 0
    assert state.has_expired_grants


async def test_grant_iap_guarda_el_original_de_apple() -> None:
    cuenta = _cuenta()
    repo = _Membresias()
    grant = await grant_iap(
        account=cuenta,
        memberships=repo,
        product_id=IAP_ANNUAL_PRODUCT,
        transaction_id="txn-anual",
        original_transaction_id="orig-anual",
        now=AHORA,
    )
    assert grant.weeks == 52
    assert grant.original_transaction_id == "orig-anual"
