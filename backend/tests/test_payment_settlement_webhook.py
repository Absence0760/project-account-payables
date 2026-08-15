"""The processor webhook must reconcile the settled amount, not just the status.

A verified HMAC authenticates the SENDER, not the CONTENT. Before this the
handler took `status: completed` at face value: it stamped the regulated
`completed_at`, captured any accepted early-pay discount off OUR authorized
number, and handed the run to the ERP sync — with no comparison against the
amount AP actually authorized. A wire that left at $50,000 against a $5,000
instruction, a partial settlement, or a mis-mapped provider integration all
reconciled clean, and the only downstream net was a bank statement someone had
to remember to upload days later.

What the handler now does on every `completed` event, and what these tests pin:

  * runs `services/payment_settlement.verify_settlement` against the reported
    amount + currency and the payment's authorized leg(s);
  * records the verdict on the SAME append-only audit row that records the
    money moving — matched, mismatched and unverified alike;
  * on a discrepancy: opens a payment-blocking `fraud_flag` (the same call
    Positive Pay makes for an ALTERED cheque) and does NOT capture the
    discount, while still recording that the payment completed;
  * on a clean settlement: behaves exactly as before.

Unit-level against the real handler coroutine with mocked AsyncSessions,
matching the sibling payment tests' established shape.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.payment_adapters import PaymentStatus

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


def _fake_request(body: bytes = b"{}", headers: dict | None = None):
    req = MagicMock()
    req.body = AsyncMock(return_value=body)
    req.headers = headers or {}
    return req


def _ctrl_session_factory(org):
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=org)
    ctrl_db = AsyncMock()
    ctrl_db.execute = AsyncMock(return_value=result)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=ctrl_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory


def _target_table(stmt) -> str:
    """The table a `select(...)` reads from.

    Dispatching on the TABLE rather than `column_descriptions[0]["entity"]`
    is what makes an aggregate work: `select(func.count()).where(Exception...)`
    has no entity in its column descriptions (the column is the function), so
    an entity-keyed fake silently falls through to the default branch and
    hands back a MagicMock where the handler expects an int.
    """
    try:
        return stmt.get_final_froms()[0].name
    except (AttributeError, IndexError, TypeError):
        return ""


def _tenant_session_factory(payment, invoice, open_exceptions: int = 0):
    """Dispatch results by the statement's target table: `payments` (the
    FOR UPDATE lock), `invoices` (the settlement verifier's target currency +
    the exception's parent), `exceptions` (the fraud_flag dedupe count)."""

    def _execute(stmt, *args, **kwargs):
        result = MagicMock()
        table = _target_table(stmt)
        if table == "invoices":
            result.scalar_one_or_none = MagicMock(return_value=invoice)
        elif table == "exceptions":
            result.scalar = MagicMock(return_value=open_exceptions)
        else:
            result.scalar_one_or_none = MagicMock(return_value=payment)
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, db


def _org(slug="acme", provider="modern_treasury"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug=slug,
        db_name=f"feoh_{slug}",
        settings={"payments": {"provider": provider, "webhook_signing_secret": "s3cret"}},
    )


def _payment(amount=Decimal("5000.00"), **overrides):
    fields = {
        "id": uuid.uuid4(),
        "invoice_id": uuid.uuid4(),
        "correlation_id": uuid.uuid4(),
        "provider_payment_id": "px_1",
        "payment_run_id": uuid.uuid4(),
        "status": "submitted",
        "completed_at": None,
        "reference": None,
        "failure_reason": None,
        "amount": amount,
        "method": "wire",
        "source_amount": None,
        "source_currency": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _invoice(currency="USD"):
    return SimpleNamespace(id=uuid.uuid4(), entity_id=uuid.uuid4(), currency=currency)


def _adapter(*, amount, currency, status=PaymentStatus.completed, event_id="evt_1"):
    adapter = MagicMock()
    adapter.parse_webhook = MagicMock(
        return_value=SimpleNamespace(
            provider_payment_id="px_1",
            event_id=event_id,
            status=status,
            reference="REF-1",
            failure_reason=None,
            amount=amount,
            currency=currency,
        )
    )
    return adapter


async def _run(org, adapter, tenant_factory, *, capture=None, exc=None, audit=None, sync=None):
    """Drive the real handler with the patch stack the sibling files use."""
    from app.api.payments import payment_webhook

    capture = capture or AsyncMock()
    exc = exc or AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    audit = audit or AsyncMock()
    sync = sync or AsyncMock()
    with (
        patch("app.database.control_session_factory", _ctrl_session_factory(org)),
        patch("app.api.payments.get_payment_adapter", return_value=adapter),
        patch("app.database.get_tenant_engine", return_value=MagicMock()),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", return_value=tenant_factory),
        patch("app.api.payments._capture_discount_offers", capture),
        patch("app.services.exception_service.create_exception", exc),
        patch("app.services.audit_dispatch.dispatch_audit", audit),
        patch("app.services.payment_erp_sync.dispatch_payment_sync", sync),
    ):
        await payment_webhook(
            tenant_slug=org.slug, provider="modern_treasury", request=_fake_request()
        )
    return {"capture": capture, "exception": exc, "audit": audit, "sync": sync}


# ---------------------------------------------------------------------------
# The clean path stays clean
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matching_settlement_is_unchanged_behaviour():
    payment = _payment(amount=Decimal("5000.00"))
    tenant_factory, db = _tenant_session_factory(payment, _invoice())

    mocks = await _run(_org(), _adapter(amount=Decimal("5000.00"), currency="USD"), tenant_factory)

    assert payment.status == "completed"
    assert payment.completed_at is not None
    db.commit.assert_awaited_once()
    # Discount capture runs; no exception raised.
    mocks["capture"].assert_awaited_once()
    mocks["exception"].assert_not_awaited()
    # The run is still handed to the ERP sync.
    mocks["sync"].assert_awaited_once()
    verdict = mocks["audit"].call_args.kwargs["details"]["settlement"]
    assert verdict["outcome"] == "matched"


# ---------------------------------------------------------------------------
# Over-settlement — the headline case
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_over_settlement_opens_a_blocking_fraud_flag_and_skips_the_discount():
    """The wire that left at $50,000 against a $5,000 instruction. The payment
    still records as completed (money moved; refusing to record it does not
    un-move it) but the discount is NOT captured and a payment-blocking
    `fraud_flag` lands in the queue."""
    payment = _payment(amount=Decimal("5000.00"))
    tenant_factory, db = _tenant_session_factory(payment, _invoice())

    mocks = await _run(_org(), _adapter(amount=Decimal("50000.00"), currency="USD"), tenant_factory)

    assert payment.status == "completed"
    db.commit.assert_awaited_once()

    mocks["capture"].assert_not_awaited()
    mocks["exception"].assert_awaited_once()
    kwargs = mocks["exception"].call_args.kwargs
    assert kwargs["exception_type"] == "fraud_flag"
    assert kwargs["severity"] == "error"
    assert "50000.00" in kwargs["description"] and "5000.00" in kwargs["description"]


@pytest.mark.asyncio
async def test_over_settlement_records_the_variance_on_the_audit_row():
    """The exception row is mutable and gets resolved; the audit row is the
    append-only, WORM-shipped evidence of what the processor said it moved."""
    payment = _payment(amount=Decimal("5000.00"))
    tenant_factory, _db = _tenant_session_factory(payment, _invoice())

    mocks = await _run(_org(), _adapter(amount=Decimal("50000.00"), currency="USD"), tenant_factory)

    details = mocks["audit"].call_args.kwargs["details"]
    assert details["status"] == "completed"
    verdict = details["settlement"]
    assert verdict["outcome"] == "amount_mismatch"
    assert verdict["settled_amount"] == "50000.00"
    assert verdict["authorized_amount"] == "5000.00"
    # Positive = the processor moved MORE than we authorized.
    assert verdict["variance"] == "45000.00"


@pytest.mark.asyncio
async def test_under_settlement_is_also_flagged():
    """A partial settlement leaves the supplier short and the invoice not
    actually paid in full — a positive-only check would miss it."""
    payment = _payment(amount=Decimal("500.00"))
    tenant_factory, _db = _tenant_session_factory(payment, _invoice())

    mocks = await _run(_org(), _adapter(amount=Decimal("250.00"), currency="USD"), tenant_factory)

    mocks["exception"].assert_awaited_once()
    assert mocks["audit"].call_args.kwargs["details"]["settlement"]["variance"] == "-250.00"


@pytest.mark.asyncio
async def test_settlement_in_an_unauthorized_currency_is_flagged():
    """1000 EUR and 1000 USD are the same NUMBER — an amount-only comparison
    would call this settled."""
    payment = _payment(amount=Decimal("1000.00"))
    tenant_factory, _db = _tenant_session_factory(payment, _invoice(currency="USD"))

    mocks = await _run(_org(), _adapter(amount=Decimal("1000.00"), currency="EUR"), tenant_factory)

    mocks["exception"].assert_awaited_once()
    assert (
        mocks["audit"].call_args.kwargs["details"]["settlement"]["outcome"] == "currency_mismatch"
    )


# ---------------------------------------------------------------------------
# Cross-currency: the source leg is a legitimate report
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_processor_reporting_the_source_leg_of_an_fx_payment_is_not_flagged():
    """A EUR invoice on a USD-home org debits `source_amount` USD. A processor
    reporting the USD side must not be treated as a discrepancy."""
    payment = _payment(
        amount=Decimal("1000.00"),
        source_amount=Decimal("1086.96"),
        source_currency="USD",
    )
    tenant_factory, _db = _tenant_session_factory(payment, _invoice(currency="EUR"))

    mocks = await _run(_org(), _adapter(amount=Decimal("1086.96"), currency="USD"), tenant_factory)

    mocks["exception"].assert_not_awaited()
    mocks["capture"].assert_awaited_once()
    verdict = mocks["audit"].call_args.kwargs["details"]["settlement"]
    assert verdict["outcome"] == "matched"
    assert verdict["authorized_leg"] == "source"


# ---------------------------------------------------------------------------
# Fail-open when the rail reports nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_provider_reporting_no_amount_settles_normally_but_records_the_blind_spot():
    """Dwolla's event body carries no amount. That is not evidence of a
    discrepancy — flagging it would open a fraud_flag on every payment on
    that rail — but it must be VISIBLE on the audit row, not silent."""
    payment = _payment(amount=Decimal("500.00"))
    tenant_factory, db = _tenant_session_factory(payment, _invoice())

    mocks = await _run(_org(), _adapter(amount=None, currency=None), tenant_factory)

    assert payment.status == "completed"
    db.commit.assert_awaited_once()
    mocks["exception"].assert_not_awaited()
    mocks["capture"].assert_awaited_once()
    verdict = mocks["audit"].call_args.kwargs["details"]["settlement"]
    assert verdict["outcome"] == "unverified"
    assert verdict["reason"] == "provider_reported_no_amount"


# ---------------------------------------------------------------------------
# Scope: only a completion is verified
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failed_event_is_not_amount_verified():
    """A `failed` event moved no money, so whatever figure it echoes
    reconciles against nothing — no verdict, no exception, and the invoice
    lookup is skipped entirely."""
    payment = _payment(amount=Decimal("500.00"))
    tenant_factory, db = _tenant_session_factory(payment, _invoice())

    mocks = await _run(
        _org(),
        _adapter(amount=Decimal("999999.00"), currency="USD", status=PaymentStatus.failed),
        tenant_factory,
    )

    assert payment.status == "failed"
    db.commit.assert_awaited_once()
    mocks["exception"].assert_not_awaited()
    mocks["capture"].assert_not_awaited()
    assert "settlement" not in mocks["audit"].call_args.kwargs["details"]


# ---------------------------------------------------------------------------
# Dedupe — one open fraud flag per invoice is the signal
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_invoice_that_already_carries_an_open_fraud_flag_is_not_double_flagged():
    """Same rule Positive Pay's own return processing uses. A second flag adds
    noise, not information — and the audit row still records the verdict."""
    payment = _payment(amount=Decimal("5000.00"))
    tenant_factory, _db = _tenant_session_factory(payment, _invoice(), open_exceptions=1)

    mocks = await _run(_org(), _adapter(amount=Decimal("50000.00"), currency="USD"), tenant_factory)

    mocks["exception"].assert_not_awaited()
    # Still not captured, and still on the append-only trail.
    mocks["capture"].assert_not_awaited()
    assert mocks["audit"].call_args.kwargs["details"]["settlement"]["outcome"] == "amount_mismatch"


@pytest.mark.asyncio
async def test_missing_invoice_row_still_settles_and_audits_without_a_queue_entry():
    """A payment whose invoice is gone has nothing to hang the flag on. It
    must not raise — the settlement still records, with an unknown target
    currency treated as a wildcard rather than a manufactured mismatch."""
    payment = _payment(amount=Decimal("5000.00"))
    tenant_factory, db = _tenant_session_factory(payment, None)

    mocks = await _run(_org(), _adapter(amount=Decimal("50000.00"), currency="USD"), tenant_factory)

    assert payment.status == "completed"
    db.commit.assert_awaited_once()
    mocks["exception"].assert_not_awaited()
    assert mocks["audit"].call_args.kwargs["details"]["settlement"]["outcome"] == "amount_mismatch"
