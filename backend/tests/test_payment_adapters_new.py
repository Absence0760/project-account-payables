"""End-to-end tests for the five new payment-processor adapters:
Stripe Treasury, Increase, Column, Dwolla, Checkeeper.

The Modern Treasury adapter already has thorough coverage in
`test_payment_adapters.py`; this file pins the contracts the new
adapters share with it plus their per-provider quirks:

  - Auth header / body shape on `create_payment` outbound request
  - Idempotency key carried through to the processor
  - Webhook signature verification with replay protection where
    applicable (Stripe + Increase use timestamped signatures;
    Column / Dwolla / Checkeeper use plain HMAC)
  - Webhook body that's tampered (signature mismatch) → None
  - `quote_payment` returns a `CorridorQuote` with provider-specific
    fees and respects `flat_fees` override
  - Unconfigured-key paths fail fast with a clear failure_reason
    (no silent default to mock)
  - Wrong-method calls fail fast with a `not supported` reason

httpx is stubbed across the board — we never hit a real provider.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.payment_adapters import (
    PaymentPayload,
    PaymentStatus,
    get_payment_adapter,
)
from app.services.payment_adapters.checkeeper import CheckeeperAdapter
from app.services.payment_adapters.column import ColumnAdapter
from app.services.payment_adapters.dwolla import DwollaAdapter
from app.services.payment_adapters.increase import IncreaseAdapter
from app.services.payment_adapters.stripe_treasury import StripeTreasuryAdapter

_DEFAULT_BANK = {"counterparty_id": "ba_test_abc123"}


def _payload(*, method="ach", amount=Decimal("100.00"), vendor_bank=_DEFAULT_BANK, currency="USD"):
    """`vendor_bank=_DEFAULT_BANK` keeps the happy-path counterparty
    by default; pass `vendor_bank={}` explicitly when a test needs
    to exercise the no-counterparty branch."""
    return PaymentPayload(
        correlation_id="cor-test-1",
        invoice_id="inv-1",
        invoice_number="INV-1",
        vendor_name="Test Vendor",
        amount=amount,
        currency=currency,
        method=method,
        description="Test payment",
        vendor_bank=vendor_bank,
        metadata={"organization_id": "org-1"},
    )


class _FakeResponse:
    def __init__(self, body, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock())


def _fake_async_client(responses: list[_FakeResponse], captured: dict | None = None):
    """Build a context-manager class that yields the responses in
    order from `client.get` / `client.post`. Records every call's
    args into `captured["calls"]` for assertions."""
    calls: list[dict] = captured.setdefault("calls", []) if captured is not None else []
    response_iter = iter(responses)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls.append({"method": "POST", "url": url, **kw})
            return next(response_iter)

        async def get(self, url, **kw):
            calls.append({"method": "GET", "url": url, **kw})
            return next(response_iter)

    return _Client


# ---------------------------------------------------------------------------
# Registration smoke test — dispatcher resolves every provider.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "provider",
    ["stripe_treasury", "increase", "column", "dwolla", "checkeeper", "modern_treasury", "mock"],
)
def test_dispatcher_resolves_every_registered_provider(provider):
    """Pin that every adapter self-registers and is reachable via
    the dispatcher. A regression that dropped the import in
    `__init__.py` would silently fall back to `mock` here."""
    adapter = get_payment_adapter({"provider": provider})
    assert adapter.provider_name == provider


# ---------------------------------------------------------------------------
# Stripe Treasury.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stripe_treasury_create_payment_builds_outbound_payment_request():
    """Happy path: posts to /v1/treasury/outbound_payments with the
    right idempotency key, amount in minor units, and ACH network
    selector. Response gives back the OutboundPayment ID."""
    captured: dict = {}
    fake_client = _fake_async_client(
        [_FakeResponse({"id": "obp_test_1", "status": "processing"})],
        captured=captured,
    )
    adapter = StripeTreasuryAdapter({"api_key": "sk_test_abc", "financial_account_id": "fa_test_1"})
    with patch("app.services.payment_adapters.stripe_treasury.httpx.AsyncClient", fake_client):
        result = await adapter.create_payment(_payload(amount=Decimal("100.00")))

    assert result.success is True
    assert result.status == PaymentStatus.processing
    assert result.provider_payment_id == "obp_test_1"
    call = captured["calls"][0]
    assert "/treasury/outbound_payments" in call["url"]
    assert call["headers"]["Idempotency-Key"] == "cor-test-1"
    assert call["headers"]["Authorization"] == "Bearer sk_test_abc"
    # Form body: amount in minor units, ACH network.
    body = call["data"]
    assert body["amount"] == "10000"
    assert body["financial_account"] == "fa_test_1"
    assert body["destination_payment_method"] == "ba_test_abc123"


@pytest.mark.asyncio
async def test_stripe_treasury_returns_clean_failure_reason_on_api_error():
    """4xx response → `failure_reason` keeps the Stripe error CODE
    only (never the message — Stripe messages can echo card-shaped
    strings)."""
    fake_client = _fake_async_client(
        [
            _FakeResponse(
                {"error": {"code": "insufficient_funds", "message": "card 4111... declined"}},
                status_code=402,
            )
        ]
    )
    adapter = StripeTreasuryAdapter({"api_key": "sk_test", "financial_account_id": "fa"})
    with patch("app.services.payment_adapters.stripe_treasury.httpx.AsyncClient", fake_client):
        result = await adapter.create_payment(_payload())
    assert result.success is False
    assert "stripe_api_error:insufficient_funds" == result.failure_reason
    # Critical: the PAN-shaped string from the message MUST NOT
    # appear in the surfaced reason.
    assert "4111" not in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_stripe_treasury_unconfigured_key_returns_specific_failure():
    """Empty api_key → fail fast with `stripe_treasury_not_configured`.
    Don't silently call the API without auth."""
    adapter = StripeTreasuryAdapter({})
    result = await adapter.create_payment(_payload())
    assert result.success is False
    assert result.failure_reason == "stripe_treasury_not_configured"


@pytest.mark.asyncio
async def test_stripe_treasury_wrong_method_rejected():
    adapter = StripeTreasuryAdapter({"api_key": "sk", "financial_account_id": "fa"})
    result = await adapter.create_payment(_payload(method="rtp"))
    assert "not supported" in (result.failure_reason or "")


def test_stripe_treasury_webhook_signature_verification_accepts_valid():
    """Stripe signature: `t=<ts>,v1=<hmac(ts.body)>`. Adapter
    returns a WebhookEvent on a valid + fresh signature."""
    adapter = StripeTreasuryAdapter({"api_key": "sk", "webhook_secret": "whsec_test"})
    body = json.dumps(
        {
            "type": "treasury.outbound_payment.posted",
            "data": {"object": {"id": "obp_1", "status": "posted"}},
            "created": 1715000000,
        }
    ).encode()
    ts = str(int(time.time()))
    sig = hmac.new(b"whsec_test", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={ts},v1={sig}"}

    event = adapter.parse_webhook(headers, body)
    assert event is not None
    assert event.provider_payment_id == "obp_1"
    assert event.status == PaymentStatus.completed


def test_stripe_treasury_webhook_rejects_tampered_body():
    adapter = StripeTreasuryAdapter({"api_key": "sk", "webhook_secret": "whsec_test"})
    body = b'{"type":"treasury.outbound_payment.posted"}'
    ts = str(int(time.time()))
    # Sign a DIFFERENT body to simulate tampering.
    bad_sig = hmac.new(
        b"whsec_test", f"{ts}.".encode() + b'{"different":true}', hashlib.sha256
    ).hexdigest()
    headers = {"Stripe-Signature": f"t={ts},v1={bad_sig}"}
    assert adapter.parse_webhook(headers, body) is None


def test_stripe_treasury_webhook_rejects_stale_timestamp():
    """Replay protection: timestamps older than 5 min are rejected."""
    adapter = StripeTreasuryAdapter({"api_key": "sk", "webhook_secret": "whsec_test"})
    body = (
        b'{"type":"treasury.outbound_payment.posted",'
        b'"data":{"object":{"id":"x","status":"posted"}}}'
    )
    old_ts = str(int(time.time()) - 10 * 60)  # 10 minutes ago
    sig = hmac.new(b"whsec_test", f"{old_ts}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"Stripe-Signature": f"t={old_ts},v1={sig}"}
    assert adapter.parse_webhook(headers, body) is None


@pytest.mark.asyncio
async def test_stripe_treasury_quote_returns_provider_fees():
    adapter = StripeTreasuryAdapter({"api_key": "sk"})
    quote = await adapter.quote_payment(_payload(method="ach"))
    assert quote.available is True
    assert quote.provider == "stripe_treasury"
    assert quote.flat_fee == Decimal("0.25")
    # Wire is more expensive.
    quote_w = await adapter.quote_payment(_payload(method="wire"))
    assert quote_w.flat_fee == Decimal("10.00")


@pytest.mark.asyncio
async def test_stripe_treasury_quote_respects_flat_fees_override():
    adapter = StripeTreasuryAdapter({"api_key": "sk", "flat_fees": {"ach": "0.05"}})
    quote = await adapter.quote_payment(_payload(method="ach"))
    assert quote.flat_fee == Decimal("0.05")


# ---------------------------------------------------------------------------
# Increase.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_increase_create_payment_routes_method_to_right_endpoint():
    """ACH → /ach_transfers; wire → /wire_transfers. Idempotency-Key
    carried; amount in minor units."""
    captured: dict = {}
    fake_client = _fake_async_client(
        [_FakeResponse({"id": "ach_transfer_1", "status": "pending_submission"})],
        captured=captured,
    )
    adapter = IncreaseAdapter({"api_key": "k", "account_id": "account_1", "sandbox": True})
    with patch("app.services.payment_adapters.increase.httpx.AsyncClient", fake_client):
        result = await adapter.create_payment(_payload(method="ach"))

    assert result.success
    assert result.status == PaymentStatus.submitted
    call = captured["calls"][0]
    assert call["url"].endswith("/ach_transfers")
    assert call["headers"]["Idempotency-Key"] == "cor-test-1"
    assert call["json"]["amount"] == 10000
    assert call["json"]["external_account_id"] == "ba_test_abc123"


@pytest.mark.asyncio
async def test_increase_create_payment_wire_endpoint():
    captured: dict = {}
    fake_client = _fake_async_client(
        [_FakeResponse({"id": "wire_transfer_1", "status": "pending_approval"})],
        captured=captured,
    )
    adapter = IncreaseAdapter({"api_key": "k", "account_id": "acc"})
    with patch("app.services.payment_adapters.increase.httpx.AsyncClient", fake_client):
        await adapter.create_payment(_payload(method="wire"))
    assert captured["calls"][0]["url"].endswith("/wire_transfers")


def test_increase_webhook_signature_verification():
    adapter = IncreaseAdapter({"api_key": "k", "webhook_secret": "wsec"})
    body = json.dumps(
        {
            "associated_object": {"id": "ach_transfer_1", "status": "complete"},
            "created_at": "2026-05-10T00:00:00Z",
        }
    ).encode()
    ts = str(int(time.time()))
    sig = hmac.new(b"wsec", f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    headers = {"Increase-Webhook-Signature": f"t={ts},v1={sig}"}
    event = adapter.parse_webhook(headers, body)
    assert event is not None
    assert event.provider_payment_id == "ach_transfer_1"
    assert event.status == PaymentStatus.completed


@pytest.mark.asyncio
async def test_increase_quote_includes_all_three_rails():
    adapter = IncreaseAdapter({"api_key": "k", "account_id": "acc"})
    ach = await adapter.quote_payment(_payload(method="ach"))
    wire = await adapter.quote_payment(_payload(method="wire"))
    check = await adapter.quote_payment(_payload(method="check"))
    assert ach.available and wire.available and check.available
    # Wire is more expensive than ACH; check is in between.
    assert wire.flat_fee > ach.flat_fee


# ---------------------------------------------------------------------------
# Column.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_column_create_payment_uses_basic_auth_with_empty_password():
    """Column auth: HTTP Basic with `<api_key>:` (empty password).
    The base64-encoded header must end with `:` post-decode."""
    captured: dict = {}
    fake_client = _fake_async_client(
        [_FakeResponse({"id": "acht_1", "status": "initiated"})],
        captured=captured,
    )
    adapter = ColumnAdapter({"api_key": "colkey_test", "bank_account_id": "bnk_1"})
    with patch("app.services.payment_adapters.column.httpx.AsyncClient", fake_client):
        await adapter.create_payment(_payload(method="ach"))

    import base64

    auth = captured["calls"][0]["headers"]["Authorization"]
    assert auth.startswith("Basic ")
    decoded = base64.b64decode(auth.removeprefix("Basic ")).decode()
    assert decoded == "colkey_test:"


@pytest.mark.asyncio
async def test_column_no_counterparty_fails_fast():
    adapter = ColumnAdapter({"api_key": "k", "bank_account_id": "bnk"})
    result = await adapter.create_payment(_payload(vendor_bank={}))
    assert result.failure_reason == "column_no_counterparty"


def test_column_webhook_plain_hmac():
    """Column uses plain HMAC-SHA256 over the body (no timestamp
    prefix) in `Column-Signature`."""
    adapter = ColumnAdapter({"api_key": "k", "webhook_secret": "wsec"})
    body = json.dumps(
        {
            "type": "transfer.settled",
            "data": {"id": "acht_1", "status": "settled"},
        }
    ).encode()
    sig = hmac.new(b"wsec", body, hashlib.sha256).hexdigest()
    event = adapter.parse_webhook({"Column-Signature": sig}, body)
    assert event is not None
    assert event.provider_payment_id == "acht_1"
    assert event.status == PaymentStatus.completed


# ---------------------------------------------------------------------------
# Dwolla.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dwolla_fetches_oauth_token_then_creates_transfer():
    """Dwolla auth flow: POST /token first, cache for 50min, then
    use the bearer on /transfers."""
    captured: dict = {}
    fake_client = _fake_async_client(
        [
            _FakeResponse({"access_token": "tok_test", "expires_in": 3600}),
            _FakeResponse(
                {}, status_code=201, headers={"Location": "https://api.dwolla.com/transfers/xfer_1"}
            ),
        ],
        captured=captured,
    )
    adapter = DwollaAdapter(
        {
            "client_id": "id",
            "client_secret": "sec",
            "source_funding_source": "https://api.dwolla.com/funding-sources/src1",
        }
    )
    with patch("app.services.payment_adapters.dwolla.httpx.AsyncClient", fake_client):
        result = await adapter.create_payment(
            _payload(
                method="ach",
                vendor_bank={"counterparty_id": "https://api.dwolla.com/funding-sources/dst1"},
            )
        )

    assert result.success is True
    assert result.provider_payment_id == "xfer_1"
    # First call: token request. Second: transfer.
    assert "/token" in captured["calls"][0]["url"]
    transfer_call = captured["calls"][1]
    assert transfer_call["headers"]["Authorization"] == "Bearer tok_test"


@pytest.mark.asyncio
async def test_dwolla_caches_token_across_calls():
    """Two payments back-to-back must NOT mint two tokens — the
    in-process cache is the single-flight guard against burning the
    1h Dwolla quota."""
    captured: dict = {}
    fake_client = _fake_async_client(
        [
            _FakeResponse({"access_token": "tok", "expires_in": 3600}),
            _FakeResponse(
                {}, status_code=201, headers={"Location": "https://api.dwolla.com/transfers/x1"}
            ),
            _FakeResponse(
                {}, status_code=201, headers={"Location": "https://api.dwolla.com/transfers/x2"}
            ),
        ],
        captured=captured,
    )
    adapter = DwollaAdapter(
        {
            "client_id": "id",
            "client_secret": "sec",
            "source_funding_source": "https://api.dwolla.com/funding-sources/src1",
        }
    )
    with patch("app.services.payment_adapters.dwolla.httpx.AsyncClient", fake_client):
        await adapter.create_payment(
            _payload(
                vendor_bank={"counterparty_id": "https://api.dwolla.com/funding-sources/dst1"},
            )
        )
        await adapter.create_payment(
            _payload(
                vendor_bank={"counterparty_id": "https://api.dwolla.com/funding-sources/dst1"},
            )
        )
    # Three calls total: 1 token + 2 transfers.
    assert len(captured["calls"]) == 3
    token_calls = [c for c in captured["calls"] if "/token" in c["url"]]
    assert len(token_calls) == 1


def test_dwolla_webhook_uses_hmac_over_raw_body():
    """Dwolla signs the raw body with HMAC-SHA256; the signature
    header is `X-Request-Signature-SHA-256`."""
    adapter = DwollaAdapter({"client_id": "i", "client_secret": "s", "webhook_secret": "wsec"})
    body = json.dumps(
        {
            "topic": "transfer_completed",
            "resourceId": "xfer_1",
            "created": "2026-05-10T00:00:00.000Z",
        }
    ).encode()
    sig = hmac.new(b"wsec", body, hashlib.sha256).hexdigest()
    event = adapter.parse_webhook({"X-Request-Signature-SHA-256": sig}, body)
    assert event is not None
    assert event.status == PaymentStatus.completed


@pytest.mark.asyncio
async def test_dwolla_wire_method_unavailable():
    """Dwolla doesn't do wires — `quote_payment(method='wire')`
    returns unavailable so the optimizer skips it."""
    adapter = DwollaAdapter({"client_id": "i", "client_secret": "s"})
    quote = await adapter.quote_payment(_payload(method="wire"))
    assert quote.available is False
    assert "ach only" in (quote.unavailable_reason or "")


# ---------------------------------------------------------------------------
# Checkeeper.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_checkeeper_requires_mailing_address_to_create_check():
    """No mailing_address on vendor_bank → fail fast. Don't ship a
    blank check."""
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    result = await adapter.create_payment(_payload(method="check", vendor_bank={}))
    assert result.failure_reason == "checkeeper_missing_mailing_address"


@pytest.mark.asyncio
async def test_checkeeper_create_check_sends_full_address():
    captured: dict = {}
    fake_client = _fake_async_client(
        [_FakeResponse({"id": "chk_1", "status": "queued", "check_number": "1001"})],
        captured=captured,
    )
    adapter = CheckeeperAdapter({"api_key": "k", "bank_account_id": "bnk"})
    payload = _payload(
        method="check",
        vendor_bank={
            "mailing_address": {
                "street": "1 Acme St",
                "city": "Anywhere",
                "state": "CA",
                "postal": "94000",
            },
        },
    )
    with patch("app.services.payment_adapters.checkeeper.httpx.AsyncClient", fake_client):
        result = await adapter.create_payment(payload)

    assert result.success is True
    assert result.provider_payment_id == "chk_1"
    assert result.reference == "1001"
    body = captured["calls"][0]["json"]
    assert body["payee"]["address"]["line1"] == "1 Acme St"
    assert body["payee"]["address"]["postal_code"] == "94000"


@pytest.mark.asyncio
async def test_checkeeper_only_supports_check_method():
    adapter = CheckeeperAdapter({"api_key": "k"})
    for method in ("ach", "wire", "rtp"):
        quote = await adapter.quote_payment(_payload(method=method))
        assert quote.available is False
        assert "check only" in (quote.unavailable_reason or "")
    quote_ok = await adapter.quote_payment(_payload(method="check"))
    assert quote_ok.available is True
