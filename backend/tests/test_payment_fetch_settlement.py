"""`fetch_settlement` — closing the two rails settlement verification couldn't reach.

Settlement-amount verification compares what a processor says it moved against
what AP authorized, but it can only compare a figure it HAS. Two paths reached
`completed` without one:

* **Dwolla**, whose status webhook is a bare `{id, topic, resourceId, _links}`
  envelope. The transfer amount is only reachable by following
  `_links.resource`, and `parse_webhook` is synchronous and sits on the
  signature-verification line, so it must not make a network call.
* **The reconciler backstop**, whose `get_payment_status` returns a bare
  `PaymentStatus` by design — so a payment it settles (precisely the case where
  the webhook never arrived) had no figure either.

Both therefore read `unverified` and, once the coverage check landed, would
discharge their invoice on a settlement nobody verified. `fetch_settlement` is
the pull counterpart to the push figure on `WebhookEvent`: the caller asks
after the signature is verified, off the synchronous path.

The contract is deliberately the same shape as `get_balance`'s — an optional
capability whose base implementation reports `available=False` — so an adapter
that never implements it is unaffected and the verdict stays `unverified`
rather than becoming an invented one. Every call site guards, because a
settlement fetch must never break the webhook recording money movement nor halt
the sweep.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.payment_adapters import PaymentStatus, SettlementReport
from app.services.payment_adapters.base import PaymentAdapter
from app.services.payment_adapters.dwolla import DwollaAdapter
from app.services.payment_adapters.mock_adapter import MockPaymentAdapter

# ---------------------------------------------------------------------------
# The optional-capability contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_base_adapter_reports_the_capability_as_unavailable():
    """An adapter that never implements it must not be forced to — and must not
    look like it reported a zero."""
    report = await PaymentAdapter({}).fetch_settlement("px_1")
    assert report.available is False
    assert report.amount is None
    assert report.unavailable_reason == "not_supported"


@pytest.mark.asyncio
async def test_mock_reports_a_configured_figure():
    """Local-first (guard rail 7): the under-settlement path is exercisable
    with no processor account."""
    adapter = MockPaymentAdapter({"settled_amount": "250.00", "settled_currency": "USD"})
    report = await adapter.fetch_settlement("px_1")
    assert report.available is True
    assert report.amount == Decimal("250.00")
    assert report.currency == "USD"


@pytest.mark.asyncio
async def test_mock_can_simulate_an_adapter_without_the_capability():
    adapter = MockPaymentAdapter({"settlement_available": False})
    assert (await adapter.fetch_settlement("px_1")).available is False


@pytest.mark.asyncio
async def test_mock_without_configuration_reports_nothing_rather_than_zero():
    assert (await MockPaymentAdapter({}).fetch_settlement("px_1")).available is False


# ---------------------------------------------------------------------------
# Dwolla — the rail the envelope couldn't serve
# ---------------------------------------------------------------------------


def _dwolla() -> DwollaAdapter:
    return DwollaAdapter({"key": "k", "secret": "s", "funding_source": "fs"})


def _response(status_code: int, payload: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=payload or {})
    return resp


def _client_returning(resp):
    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
async def test_dwolla_follows_the_transfer_for_the_amount():
    """Dwolla reports decimal strings, not minor units — no exponent scaling."""
    adapter = _dwolla()
    payload = {"status": "processed", "amount": {"value": "125.00", "currency": "USD"}}
    with (
        patch.object(DwollaAdapter, "_get_token", AsyncMock(return_value="tok")),
        patch("httpx.AsyncClient", _client_returning(_response(200, payload))),
    ):
        report = await adapter.fetch_settlement("xfer_1")

    assert report.available is True
    assert report.amount == Decimal("125.00")
    assert report.currency == "USD"


@pytest.mark.asyncio
async def test_dwolla_without_credentials_reports_unavailable():
    with patch.object(DwollaAdapter, "_get_token", AsyncMock(return_value=None)):
        report = await _dwolla().fetch_settlement("xfer_1")
    assert report.available is False
    assert report.unavailable_reason == "dwolla_not_configured"


@pytest.mark.asyncio
async def test_dwolla_transport_failure_is_unavailable_not_an_exception():
    """Best-effort by contract — and the reason carries the exception CLASS
    only, never its message, which can embed the URL."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("boom"))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with (
        patch.object(DwollaAdapter, "_get_token", AsyncMock(return_value="tok")),
        patch("httpx.AsyncClient", MagicMock(return_value=ctx)),
    ):
        report = await _dwolla().fetch_settlement("xfer_1")

    assert report.available is False
    assert report.unavailable_reason == "dwolla_transport_error:ConnectError"
    assert "boom" not in (report.unavailable_reason or "")


@pytest.mark.asyncio
async def test_dwolla_api_error_is_unavailable():
    with (
        patch.object(DwollaAdapter, "_get_token", AsyncMock(return_value="tok")),
        patch("httpx.AsyncClient", _client_returning(_response(404))),
    ):
        report = await _dwolla().fetch_settlement("xfer_1")
    assert report.available is False
    assert report.unavailable_reason == "dwolla_api_error:404"


@pytest.mark.asyncio
async def test_dwolla_body_without_an_amount_is_unavailable():
    with (
        patch.object(DwollaAdapter, "_get_token", AsyncMock(return_value="tok")),
        patch("httpx.AsyncClient", _client_returning(_response(200, {"status": "processed"}))),
    ):
        report = await _dwolla().fetch_settlement("xfer_1")
    assert report.available is False


# ---------------------------------------------------------------------------
# Call site 1 — the webhook fallback
# ---------------------------------------------------------------------------


def _reuse_webhook_harness():
    """Borrow the sibling file's fakes rather than duplicating them."""
    from tests import test_payment_settlement_webhook as harness

    return harness


@pytest.mark.asyncio
async def test_webhook_falls_back_to_the_fetch_when_the_event_carries_no_amount():
    h = _reuse_webhook_harness()
    payment = h._payment(amount=Decimal("5000.00"))
    tenant_factory, _ = h._tenant_session_factory(payment, h._invoice())

    adapter = h._adapter(amount=None, currency=None)
    adapter.fetch_settlement = AsyncMock(
        return_value=SettlementReport(available=True, amount=Decimal("4200.00"), currency="USD")
    )

    mocks = await h._run(h._org(), adapter, tenant_factory)

    adapter.fetch_settlement.assert_awaited_once()
    # The figure the FETCH returned is what got verified and persisted.
    assert payment.settled_amount == Decimal("4200.00")
    verdict = mocks["audit"].call_args.kwargs["details"]["settlement"]
    assert verdict["outcome"] == "amount_mismatch"


@pytest.mark.asyncio
async def test_webhook_does_not_fetch_when_the_event_already_carries_the_amount():
    """No redundant network call on the rails that already report it."""
    h = _reuse_webhook_harness()
    payment = h._payment(amount=Decimal("5000.00"))
    tenant_factory, _ = h._tenant_session_factory(payment, h._invoice())

    adapter = h._adapter(amount=Decimal("5000.00"), currency="USD")
    adapter.fetch_settlement = AsyncMock()

    await h._run(h._org(), adapter, tenant_factory)

    adapter.fetch_settlement.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_fetch_leaves_the_settlement_unverified_and_the_webhook_intact():
    """The guarantee that makes this safe to call on the money path at all."""
    h = _reuse_webhook_harness()
    payment = h._payment(amount=Decimal("5000.00"))
    tenant_factory, db = h._tenant_session_factory(payment, h._invoice())

    adapter = h._adapter(amount=None, currency=None)
    adapter.fetch_settlement = AsyncMock(side_effect=RuntimeError("processor down"))

    mocks = await h._run(h._org(), adapter, tenant_factory)

    # Webhook still completed the payment and committed.
    assert payment.status == "completed"
    db.commit.assert_awaited_once()
    # ...and the blind spot is recorded honestly rather than invented.
    assert payment.settled_amount is None
    verdict = mocks["audit"].call_args.kwargs["details"]["settlement"]
    assert verdict["outcome"] == "unverified"


# ---------------------------------------------------------------------------
# Call site 2 — the reconciler backstop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconciler_records_the_settled_figure_when_it_settles_a_payment():
    from app.services import payment_reconciler

    payment = SimpleNamespace(
        id=uuid.uuid4(),
        provider_payment_id="px_1",
        payment_run_id=uuid.uuid4(),
        status="submitted",
        completed_at=None,
        settled_amount=None,
        settled_currency=None,
        submitted_at=None,
        amount=Decimal("500.00"),
        correlation_id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        method="ach",
    )
    adapter = MagicMock()
    adapter.provider_name = "mock"
    adapter.get_payment_status = AsyncMock(return_value=PaymentStatus.completed)
    adapter.fetch_settlement = AsyncMock(
        return_value=SettlementReport(available=True, amount=Decimal("250.00"), currency="USD")
    )

    await payment_reconciler._settle_from_poll(  # type: ignore[attr-defined]
        payment=payment, adapter=adapter
    )

    assert payment.settled_amount == Decimal("250.00")
    assert payment.settled_currency == "USD"


@pytest.mark.asyncio
async def test_reconciler_settlement_fetch_failure_leaves_the_columns_null():
    from app.services import payment_reconciler

    payment = SimpleNamespace(
        id=uuid.uuid4(),
        provider_payment_id="px_1",
        settled_amount=None,
        settled_currency=None,
    )
    adapter = MagicMock()
    adapter.provider_name = "mock"
    adapter.fetch_settlement = AsyncMock(side_effect=RuntimeError("processor down"))

    await payment_reconciler._settle_from_poll(  # type: ignore[attr-defined]
        payment=payment, adapter=adapter
    )

    assert payment.settled_amount is None
