"""Voiding a card payment must be able to FINISH — including its card leg.

The void's card-cancel leg is best-effort so a card-provider outage cannot block
the accounting void. But the outcome used to land only on the `payment.voided`
audit row, so an operator who voided a card payment could not tell whether the
card was actually closed at the provider — and a leg that failed left a live,
bearer-spendable card behind a payment the books call `voided`, with nothing in
the app able to close it.

Two halves, both exercised here:

  * the outcome + its VERDICT (`card_issuance.card_cancel_disposition`) travel on
    `PaymentResponse`, so the void dialog can say what happened; and
  * `POST /api/payments/{id}/void/retry-card-cancel` re-attempts ONLY the card
    leg of an already-voided payment — the remedy sits ON the void rather than
    beside it (`docs/decisions.md` §96, §130).

These run against a live Postgres so the row locks, the audit trail and the
`uq_virtual_cards_one_live_per_invoice` slot are the real ones.
"""

from __future__ import annotations

import contextlib
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun
from app.models.vendor import Vendor
from app.models.virtual_card import VirtualCard
from app.models.workflow import AuditLog

# No module-level `pytest.mark.asyncio`: `asyncio_mode = "auto"` already drives
# the async tests, and a blanket mark would warn on the two pure ones below.
TENANT = "a"


# ---------------------------------------------------------------------------
# The verdict is pure — no DB needed
# ---------------------------------------------------------------------------


def test_disposition_classifies_every_outcome_the_cancel_leg_can_produce():
    from app.services.card_issuance import card_cancel_disposition

    # Closed: the card is dead at the provider, from either spelling.
    assert card_cancel_disposition("card_cancelled") == "closed"
    assert card_cancel_disposition("cancelled") == "closed"
    assert card_cancel_disposition("card_already_cancelled") == "closed"

    # Nothing to close.
    assert card_cancel_disposition("no_card_linked") == "no_card"

    # Spent money cannot be un-spent — a retry can never help.
    assert card_cancel_disposition("card_already_charged") == "not_closed_final"

    # Every failure of the provider leg leaves a LIVE card and is retryable.
    for outcome in (
        "cards_not_configured",
        "card_provider_not_configured",
        "card_cancel_rejected",
        "card_cancel_error:ReadTimeout",
    ):
        assert card_cancel_disposition(outcome) == "not_closed_retryable", outcome

    # The leg never ran (not a card payment / not a void read). NOT `closed` —
    # "we never asked" is not "it is shut" (decisions §34).
    assert card_cancel_disposition(None) is None


def test_an_unknown_outcome_tag_is_retryable_never_closed():
    """A tag added to `cancel_card_at_provider` later must surface, not pass.

    Classifying the unknown as `closed` would report a live card as shut — the
    exact direction this whole feature exists to prevent.
    """
    from app.services.card_issuance import card_cancel_disposition

    assert card_cancel_disposition("some_future_failure_mode") == "not_closed_retryable"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _user(uid: uuid.UUID):
    return SimpleNamespace(id=uid, full_name="Void Tester", roles=["admin"])


def _org(org_id: uuid.UUID):
    return SimpleNamespace(
        id=org_id,
        name="PyTest",
        slug="pytesta",
        settings={
            "payments": {"provider": "mock"},
            # BYOK + explicit provider keeps the dispatcher on the in-process
            # mock adapter (no network, no credential) — local-first.
            "cards": {
                "enabled": True,
                "program_type": "byok",
                "provider": "mock",
                "region": "US",
                "api_key": "test",
            },
        },
    )


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_invoice(mk, org_id: uuid.UUID, *, amount: Decimal):
    inv_id = uuid.uuid4()
    vendor_id = uuid.uuid4()
    corr = uuid.uuid4()
    async with mk() as s:
        ent = await _default_entity_id(s)
        s.add(Vendor(id=vendor_id, name="Card Vendor", organization_id=org_id))
        s.add(
            Invoice(
                id=inv_id,
                entity_id=ent,
                invoice_number=f"VOIDCARD-{uuid.uuid4().hex[:8]}",
                vendor_name="Card Vendor",
                vendor_id=vendor_id,
                amount=amount,
                currency="USD",
                status=InvoiceStatus.payment_scheduled,
                organization_id=org_id,
                correlation_id=corr,
            )
        )
        await s.commit()
    return SimpleNamespace(id=inv_id, correlation_id=corr, vendor_id=vendor_id, entity_id=ent)


async def _seed_payment(
    mk,
    org_id: uuid.UUID,
    inv,
    *,
    amount: Decimal,
    creator_id,
    method: str = "virtual_card",
    status: str = "completed",
) -> tuple[uuid.UUID, uuid.UUID]:
    run_id = uuid.uuid4()
    pay_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            PaymentRun(
                id=run_id,
                organization_id=org_id,
                status="submitted",
                total_amount=amount,
                initiated_by=creator_id,
                requires_cfo_approval=False,
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=pay_id,
                invoice_id=inv.id,
                payment_run_id=run_id,
                amount=amount,
                method=method,
                status=status,
                correlation_id=inv.correlation_id,
            )
        )
        await s.commit()
    return run_id, pay_id


def _card(inv, org_id: uuid.UUID, *, provider_card_id: str, amount: Decimal, status="created"):
    return VirtualCard(
        invoice_id=inv.id,
        organization_id=org_id,
        entity_id=inv.entity_id,
        vendor_id=inv.vendor_id,
        correlation_id=inv.correlation_id,
        card_provider="mock",
        provider_card_id=provider_card_id,
        last_four="9999",
        amount_limit=amount,
        currency="USD",
        status=status,
    )


@contextlib.contextmanager
def _ambient_patches(*extra):
    """Silence the surrounding machinery so each test is about the card leg."""
    with contextlib.ExitStack() as stack:
        for ctx in (
            patch("app.api.payments.transition_invoice", new_callable=AsyncMock),
            patch("app.services.payment_erp_sync.dispatch_payment_sync", new_callable=AsyncMock),
            *extra,
        ):
            stack.enter_context(ctx)
        yield


async def _void(realdb, pay_id, org_id, actor_id, *, reason="duplicate run"):
    from app.api.payments import VoidPaymentRequest, void_payment

    async with realdb.sessionmaker(TENANT)() as db:
        return await void_payment(
            payment_id=pay_id,
            body=VoidPaymentRequest(reason=reason),
            db=db,
            org=_org(org_id),
            user=_user(actor_id),
            entity_id=None,
        )


async def _retry(realdb, pay_id, org_id, actor_id):
    from app.api.payments import retry_void_card_cancel

    async with realdb.sessionmaker(TENANT)() as db:
        return await retry_void_card_cancel(
            payment_id=pay_id,
            db=db,
            org=_org(org_id),
            user=_user(actor_id),
            entity_id=None,
        )


async def _audit_rows(mk, entity_id, action):
    async with mk() as s:
        return (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == entity_id, AuditLog.action == action
                    )
                )
            )
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# 1. The void reports what happened to the card
# ---------------------------------------------------------------------------


async def test_a_failed_card_leg_is_visible_on_the_void_response(realdb):
    """A provider that refuses the close leaves a LIVE card. The void still
    succeeds (the accounting intent stands) but the response says so, rather
    than letting the operator read a clean 200 as "the card is shut"."""
    from app.services.card_adapters.mock_adapter import MockCardAdapter

    info = realdb.info(TENANT)
    org_id = info.org_id
    mk = realdb.sessionmaker(TENANT)
    amount = Decimal("100.00")

    inv = await _seed_invoice(mk, org_id, amount=amount)
    _run_id, pay_id = await _seed_payment(
        mk, org_id, inv, amount=amount, creator_id=info.users["ap_manager"]
    )
    async with mk() as s:
        card = _card(inv, org_id, provider_card_id="card_refused", amount=amount)
        card.payment_id = pay_id
        s.add(card)
        await s.commit()
        card_id = card.id

    async def _refuse(self, provider_card_id):
        return False

    with _ambient_patches(patch.object(MockCardAdapter, "cancel_card", _refuse)):
        result = await _void(realdb, pay_id, org_id, info.users["admin"])

    assert result.void_card_outcome == "card_cancel_rejected"
    assert result.void_card_disposition == "not_closed_retryable"

    async with mk() as s:
        card = (await s.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one()
        pay = (await s.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()

    # The books moved; the card did not. That divergence is precisely what the
    # response now reports.
    assert pay.status == "voided"
    assert card.status == "created"
    # Never marked cancelled on an unconfirmed close (fail-safe direction).
    assert not await _audit_rows(mk, card_id, "card.cancelled")


async def test_a_spent_card_is_reported_final_not_retryable(realdb):
    """A charged card cannot be un-spent. It must be SHOWN (the card was not
    closed) but never offered a retry that can only fail forever."""
    info = realdb.info(TENANT)
    org_id = info.org_id
    mk = realdb.sessionmaker(TENANT)
    amount = Decimal("100.00")

    inv = await _seed_invoice(mk, org_id, amount=amount)
    _run_id, pay_id = await _seed_payment(
        mk, org_id, inv, amount=amount, creator_id=info.users["ap_manager"]
    )
    async with mk() as s:
        card = _card(inv, org_id, provider_card_id="card_spent", amount=amount, status="charged")
        card.payment_id = pay_id
        card.amount_charged = amount
        s.add(card)
        await s.commit()

    with _ambient_patches():
        result = await _void(realdb, pay_id, org_id, info.users["admin"])

    assert result.void_card_outcome == "card_already_charged"
    assert result.void_card_disposition == "not_closed_final"


async def test_a_card_payment_with_no_linked_card_reports_no_card(realdb):
    """`no_card` is its own verdict — there is nothing to close and nothing to
    chase, so the dialog must not raise an alarm about a card that never was."""
    info = realdb.info(TENANT)
    org_id = info.org_id
    mk = realdb.sessionmaker(TENANT)
    amount = Decimal("100.00")

    inv = await _seed_invoice(mk, org_id, amount=amount)
    _run_id, pay_id = await _seed_payment(
        mk, org_id, inv, amount=amount, creator_id=info.users["ap_manager"]
    )

    with _ambient_patches():
        result = await _void(realdb, pay_id, org_id, info.users["admin"])

    assert result.void_card_outcome == "no_card_linked"
    assert result.void_card_disposition == "no_card"


async def test_the_void_closes_the_live_card_not_an_older_cancelled_one(realdb):
    """`virtual_cards.payment_id` is not unique — a cancel-then-reissue leaves
    the dead row pointing at the same payment. An unordered `LIMIT 1` could pick
    it and report `card_already_cancelled` while the SPENDABLE card stayed
    open."""
    info = realdb.info(TENANT)
    org_id = info.org_id
    mk = realdb.sessionmaker(TENANT)
    amount = Decimal("100.00")

    inv = await _seed_invoice(mk, org_id, amount=amount)
    _run_id, pay_id = await _seed_payment(
        mk, org_id, inv, amount=amount, creator_id=info.users["ap_manager"]
    )
    async with mk() as s:
        dead = _card(inv, org_id, provider_card_id="card_dead", amount=amount, status="cancelled")
        dead.payment_id = pay_id
        live = _card(inv, org_id, provider_card_id="card_live", amount=amount)
        live.payment_id = pay_id
        s.add(dead)
        s.add(live)
        await s.commit()
        live_id = live.id

    with _ambient_patches():
        result = await _void(realdb, pay_id, org_id, info.users["admin"])

    assert result.void_card_outcome == "card_cancelled"
    assert result.void_card_disposition == "closed"

    async with mk() as s:
        live = (await s.execute(select(VirtualCard).where(VirtualCard.id == live_id))).scalar_one()
    assert live.status == "cancelled"


# ---------------------------------------------------------------------------
# 2. The retry — the remedy ON the void
# ---------------------------------------------------------------------------


async def test_retry_closes_the_card_the_void_could_not(realdb):
    """The whole point: after a failed leg, the operator can finish the job."""
    from app.services.card_adapters.mock_adapter import MockCardAdapter

    info = realdb.info(TENANT)
    org_id = info.org_id
    mk = realdb.sessionmaker(TENANT)
    amount = Decimal("100.00")

    inv = await _seed_invoice(mk, org_id, amount=amount)
    _run_id, pay_id = await _seed_payment(
        mk, org_id, inv, amount=amount, creator_id=info.users["ap_manager"]
    )
    async with mk() as s:
        card = _card(inv, org_id, provider_card_id="card_retry", amount=amount)
        card.payment_id = pay_id
        s.add(card)
        await s.commit()
        card_id = card.id

    async def _boom(self, provider_card_id):
        raise RuntimeError("provider unreachable")

    with _ambient_patches(patch.object(MockCardAdapter, "cancel_card", _boom)):
        voided = await _void(realdb, pay_id, org_id, info.users["admin"])

    assert voided.void_card_outcome == "card_cancel_error:RuntimeError"
    assert voided.void_card_disposition == "not_closed_retryable"

    # The provider recovers; the operator retries from the dialog.
    with _ambient_patches():
        retried = await _retry(realdb, pay_id, org_id, info.users["admin"])

    assert retried.void_card_outcome == "card_cancelled"
    assert retried.void_card_disposition == "closed"
    # The retry re-attempts the CARD leg only — it never re-asks the rail, so it
    # must not claim an adapter outcome it did not obtain.
    assert retried.void_adapter_outcome is None
    # And it moves no money: the payment stays voided, the invoice untouched.
    assert retried.status == "voided"

    async with mk() as s:
        card = (await s.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one()
    assert card.status == "cancelled"

    card_audits = await _audit_rows(mk, card_id, "card.cancelled")
    assert len(card_audits) == 1
    # The trail distinguishes the retry from the original attempt.
    assert card_audits[0].details["via"] == "payment_void_retry"
    # PII: the last four only, never a PAN.
    assert card_audits[0].details["last_four"] == "9999"

    attempts = await _audit_rows(mk, pay_id, "payment.void_card_cancel_retried")
    assert len(attempts) == 1
    assert attempts[0].details == {
        "card_outcome": "card_cancelled",
        "card_disposition": "closed",
    }


async def test_retry_is_idempotent_a_second_one_is_a_no_op_not_an_error(realdb):
    """A repeat retry must not error, must not re-ask the provider, and must not
    write a second `card.cancelled` row — the card is already shut."""
    from app.services.card_adapters.mock_adapter import MockCardAdapter

    info = realdb.info(TENANT)
    org_id = info.org_id
    mk = realdb.sessionmaker(TENANT)
    amount = Decimal("100.00")

    inv = await _seed_invoice(mk, org_id, amount=amount)
    _run_id, pay_id = await _seed_payment(
        mk, org_id, inv, amount=amount, creator_id=info.users["ap_manager"]
    )
    async with mk() as s:
        card = _card(inv, org_id, provider_card_id="card_idem", amount=amount)
        card.payment_id = pay_id
        s.add(card)
        await s.commit()
        card_id = card.id

    async def _refuse(self, provider_card_id):
        return False

    with _ambient_patches(patch.object(MockCardAdapter, "cancel_card", _refuse)):
        await _void(realdb, pay_id, org_id, info.users["admin"])

    with _ambient_patches():
        first = await _retry(realdb, pay_id, org_id, info.users["admin"])
    assert first.void_card_outcome == "card_cancelled"

    provider_calls = 0
    real_cancel = MockCardAdapter.cancel_card

    async def _spy(self, provider_card_id):
        nonlocal provider_calls
        provider_calls += 1
        return await real_cancel(self, provider_card_id)

    with _ambient_patches(patch.object(MockCardAdapter, "cancel_card", _spy)):
        second = await _retry(realdb, pay_id, org_id, info.users["admin"])

    assert second.void_card_outcome == "card_already_cancelled"
    assert second.void_card_disposition == "closed"
    assert provider_calls == 0, "an already-cancelled card must short-circuit before the provider"

    assert len(await _audit_rows(mk, card_id, "card.cancelled")) == 1
    # The ATTEMPT is still recorded both times — an operator needs to see that a
    # retry happened, not only the retries that changed something.
    assert len(await _audit_rows(mk, pay_id, "payment.void_card_cancel_retried")) == 2


async def test_retry_refuses_a_payment_that_was_never_voided(realdb):
    """The remedy sits ON the void (decisions §96): it may only ever FINISH a
    reversal the books already recorded, never close a card while the payment
    and its invoice still claim money is in flight."""
    info = realdb.info(TENANT)
    org_id = info.org_id
    mk = realdb.sessionmaker(TENANT)
    amount = Decimal("100.00")

    inv = await _seed_invoice(mk, org_id, amount=amount)
    _run_id, pay_id = await _seed_payment(
        mk, org_id, inv, amount=amount, creator_id=info.users["ap_manager"]
    )
    async with mk() as s:
        card = _card(inv, org_id, provider_card_id="card_live_payment", amount=amount)
        card.payment_id = pay_id
        s.add(card)
        await s.commit()
        card_id = card.id

    with pytest.raises(HTTPException) as exc:
        await _retry(realdb, pay_id, org_id, info.users["admin"])
    assert exc.value.status_code == 409

    async with mk() as s:
        card = (await s.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one()
    assert card.status == "created", "the live payment's card must be untouched"


async def test_retry_refuses_a_payment_that_never_issued_a_card(realdb):
    info = realdb.info(TENANT)
    org_id = info.org_id
    mk = realdb.sessionmaker(TENANT)
    amount = Decimal("100.00")

    inv = await _seed_invoice(mk, org_id, amount=amount)
    _run_id, pay_id = await _seed_payment(
        mk, org_id, inv, amount=amount, creator_id=info.users["ap_manager"], method="ach"
    )

    with _ambient_patches():
        voided = await _void(realdb, pay_id, org_id, info.users["admin"])
    # Not a card payment — the leg never ran, so there is no verdict to render.
    assert voided.void_card_outcome is None
    assert voided.void_card_disposition is None

    with pytest.raises(HTTPException) as exc:
        await _retry(realdb, pay_id, org_id, info.users["admin"])
    assert exc.value.status_code == 409
