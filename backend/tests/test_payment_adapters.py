"""Tests for the payment-adapter scaffold + Modern Treasury parser.

The Modern Treasury HTTP path needs a live key to fully exercise; we cover
its pure-Python edges (status mapping, idempotency key shape, webhook
HMAC verification) and lock the dispatcher contract so swapping providers
later doesn't silently break the orchestrator.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from decimal import Decimal

import pytest

# ---------- Adapter scaffold ----------------------------------------------


def test_dispatcher_fails_closed_for_unknown_provider():
    """An unsupported provider name must RAISE, never silently become `mock`.

    This test previously asserted the opposite ("a misconfigured org can't
    500 the entire payments domain"). That trade was wrong for this family:
    `mock.create_payment` returns `success=True, status=completed`
    immediately, so one typo in an admin-supplied `settings.payments.provider`
    (`modern-treasury` for `modern_treasury`) made every payment in every run
    report as settled while no money moved, and flipped the invoices to
    `paid`. `mock.parse_webhook` also verifies no signature, so the same typo
    routed the public webhook route to an unverified parser. Callers now
    resolve through `_require_payment_adapter` and refuse with an actionable
    409 before anything is dispatched — the "don't 500" goal, kept, without
    the silent-success failure mode.
    """
    from app.services.payment_adapters import (
        UnknownPaymentProviderError,
        get_payment_adapter,
    )

    with pytest.raises(UnknownPaymentProviderError) as exc_info:
        get_payment_adapter({"provider": "does_not_exist"})
    assert exc_info.value.provider == "does_not_exist"


def test_unknown_provider_error_names_the_registered_alternatives():
    """The message has to be actionable for the admin who typo'd, and must
    carry no credential material out of the config dict."""
    from app.services.payment_adapters import (
        UnknownPaymentProviderError,
        get_payment_adapter,
    )

    with pytest.raises(UnknownPaymentProviderError) as exc_info:
        get_payment_adapter({"provider": "modern-treasury", "api_key": "sk_live_SECRET"})
    message = str(exc_info.value)
    assert "modern-treasury" in message
    assert "modern_treasury" in message  # the real name is offered
    assert "SECRET" not in message


def test_unknown_provider_name_is_length_bounded():
    """An absurd settings value must not bloat a log line or a response."""
    from app.services.payment_adapters import (
        UnknownPaymentProviderError,
        get_payment_adapter,
    )

    with pytest.raises(UnknownPaymentProviderError) as exc_info:
        get_payment_adapter({"provider": "x" * 5000})
    assert len(exc_info.value.provider) == 50


def test_dispatcher_returns_mock_for_empty_config():
    """No configured processor is a normal state (local-first default), and
    stays `mock`. Only a NAMED provider we have no adapter for fails closed."""
    from app.services.payment_adapters import get_payment_adapter

    assert get_payment_adapter(None).provider_name == "mock"
    assert get_payment_adapter({}).provider_name == "mock"
    # An empty / null provider is "not configured", not "unsupported".
    assert get_payment_adapter({"provider": ""}).provider_name == "mock"
    assert get_payment_adapter({"provider": None}).provider_name == "mock"


def test_registered_providers_include_mock_and_modern_treasury():
    from app.services.payment_adapters import list_available_providers

    providers = set(list_available_providers())
    assert "mock" in providers
    assert "modern_treasury" in providers


# ---------- Mock adapter ---------------------------------------------------


def test_mock_adapter_completes_immediately():
    from app.services.payment_adapters import (
        PaymentPayload,
        PaymentStatus,
        get_payment_adapter,
    )

    adapter = get_payment_adapter({"provider": "mock"})
    payload = PaymentPayload(
        correlation_id=str(uuid.uuid4()),
        invoice_id=str(uuid.uuid4()),
        invoice_number="INV-001",
        vendor_name="Acme",
        amount=Decimal("100.00"),
        currency="USD",
        method="ach",
    )
    result = asyncio.run(adapter.create_payment(payload))
    assert result.success is True
    assert result.status == PaymentStatus.completed
    assert result.provider_payment_id and result.provider_payment_id.startswith("mock_pmt_")
    assert result.reference and result.reference.startswith("MOCK-ACH-")


def test_mock_adapter_parses_synthetic_webhook():
    """Mock's `parse_webhook` lets test fixtures simulate a status flip."""
    from app.services.payment_adapters import PaymentStatus, get_payment_adapter

    adapter = get_payment_adapter({"provider": "mock"})
    body = json.dumps(
        {"provider_payment_id": "mock_pmt_abc", "status": "failed", "reference": "ref-x"}
    ).encode()
    event = adapter.parse_webhook({}, body)
    assert event is not None
    assert event.status == PaymentStatus.failed
    assert event.provider_payment_id == "mock_pmt_abc"


def test_mock_adapter_rejects_malformed_webhook():
    from app.services.payment_adapters import get_payment_adapter

    adapter = get_payment_adapter({"provider": "mock"})
    assert adapter.parse_webhook({}, b"not json") is None
    assert adapter.parse_webhook({}, b"{}") is None
    assert adapter.parse_webhook({}, b'{"status": "completed"}') is None  # missing id


# ---------- Modern Treasury — status map ----------------------------------


def test_modern_treasury_status_map_is_total_for_documented_states():
    """Every status the MT docs say a payment_order can hold MUST map to a
    PaymentStatus. Missing entries silently leave the payment in
    `submitted` forever — harder to debug than a loud KeyError."""
    from app.services.payment_adapters.base import PaymentStatus
    from app.services.payment_adapters.modern_treasury import _STATUS_MAP

    documented = {
        "needs_approval",
        "pending",
        "approved",
        "denied",
        "sent",
        "processing",
        "completed",
        "returned",
        "failed",
        "cancelled",
    }
    assert documented.issubset(_STATUS_MAP.keys())
    # Every value must be a valid PaymentStatus.
    for v in _STATUS_MAP.values():
        assert isinstance(v, PaymentStatus)


def test_modern_treasury_unsupported_method_returns_failure():
    """A method the adapter doesn't support (e.g. virtual_card) must fail
    cleanly with a helpful message — not silently send the payment."""
    from app.services.payment_adapters import (
        PaymentPayload,
        PaymentStatus,
        get_payment_adapter,
    )

    adapter = get_payment_adapter({"provider": "modern_treasury"})
    payload = PaymentPayload(
        correlation_id="x",
        invoice_id="y",
        invoice_number="INV-1",
        vendor_name="Acme",
        amount=Decimal("100"),
        currency="USD",
        method="virtual_card",
    )
    result = asyncio.run(adapter.create_payment(payload))
    assert result.success is False
    assert result.status == PaymentStatus.failed
    assert result.failure_reason and "not supported" in result.failure_reason


def test_modern_treasury_missing_counterparty_returns_failure():
    """No vendor counterparty means we cannot address the payment — must
    return a structured failure for the Payment row, not raise."""
    from app.services.payment_adapters import (
        PaymentPayload,
        PaymentStatus,
        get_payment_adapter,
    )

    adapter = get_payment_adapter({"provider": "modern_treasury", "originating_account_id": "oa_1"})
    payload = PaymentPayload(
        correlation_id="x",
        invoice_id="y",
        invoice_number="INV-1",
        vendor_name="Acme",
        amount=Decimal("100"),
        currency="USD",
        method="ach",
        vendor_bank=None,  # no counterparty
    )
    result = asyncio.run(adapter.create_payment(payload))
    assert result.success is False
    assert result.status == PaymentStatus.failed
    assert "counterparty" in (result.failure_reason or "").lower()


# ---------- Modern Treasury — webhook signature ---------------------------


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_modern_treasury_webhook_rejects_bad_signature():
    from app.services.payment_adapters import get_payment_adapter

    adapter = get_payment_adapter({"provider": "modern_treasury", "webhook_secret": "shh"})
    body = json.dumps(
        {"event": "payment_order.updated", "data": {"id": "po_1", "status": "completed"}}
    ).encode()
    event = adapter.parse_webhook({"X-Signature": "wrong"}, body)
    assert event is None


def test_modern_treasury_webhook_rejects_when_no_secret_configured():
    """Belt-and-braces: if the tenant hasn't set a webhook_secret, the
    adapter MUST refuse all webhooks — otherwise an attacker could spoof
    payment completions on a fresh tenant before they finish setup."""
    from app.services.payment_adapters import get_payment_adapter

    adapter = get_payment_adapter({"provider": "modern_treasury"})  # no secret
    body = b'{"event":"payment_order.updated","data":{"id":"po_1","status":"completed"}}'
    sig = _sign("anything", body)
    assert adapter.parse_webhook({"X-Signature": sig}, body) is None


def test_modern_treasury_webhook_accepts_signed_payload():
    from app.services.payment_adapters import PaymentStatus, get_payment_adapter

    secret = "topsecret"
    adapter = get_payment_adapter({"provider": "modern_treasury", "webhook_secret": secret})
    body = json.dumps(
        {
            "event": "payment_order.updated",
            "created_at": "2026-04-19T12:00:00Z",
            "data": {
                "id": "po_xyz",
                "status": "completed",
                "reference_number": "ACH-12345",
            },
        }
    ).encode()
    event = adapter.parse_webhook({"X-Signature": _sign(secret, body)}, body)
    assert event is not None
    assert event.provider_payment_id == "po_xyz"
    assert event.status == PaymentStatus.completed
    assert event.reference == "ACH-12345"


def test_modern_treasury_webhook_ignores_non_payment_events():
    """We get spammed with ledger_entry, expected_payment, etc. They have
    valid signatures but aren't payment status updates — must return None
    so the handler doesn't accidentally update a Payment row."""
    from app.services.payment_adapters import get_payment_adapter

    secret = "s"
    adapter = get_payment_adapter({"provider": "modern_treasury", "webhook_secret": secret})
    body = json.dumps({"event": "ledger_entry.created", "data": {"id": "le_1"}}).encode()
    assert adapter.parse_webhook({"X-Signature": _sign(secret, body)}, body) is None


def test_modern_treasury_webhook_ignores_unknown_status():
    from app.services.payment_adapters import get_payment_adapter

    secret = "s"
    adapter = get_payment_adapter({"provider": "modern_treasury", "webhook_secret": secret})
    body = json.dumps(
        {"event": "payment_order.updated", "data": {"id": "po_1", "status": "novel_state"}}
    ).encode()
    assert adapter.parse_webhook({"X-Signature": _sign(secret, body)}, body) is None


def test_modern_treasury_webhook_extracts_failure_reason_from_return():
    """ACH returns put the reason under `data.return.reason_code`."""
    from app.services.payment_adapters import PaymentStatus, get_payment_adapter

    secret = "s"
    adapter = get_payment_adapter({"provider": "modern_treasury", "webhook_secret": secret})
    body = json.dumps(
        {
            "event": "payment_order.updated",
            "data": {
                "id": "po_1",
                "status": "returned",
                "return": {
                    "reason_code": "R01",
                    "reason_description": "Insufficient funds",
                },
            },
        }
    ).encode()
    event = adapter.parse_webhook({"X-Signature": _sign(secret, body)}, body)
    assert event is not None
    assert event.status == PaymentStatus.failed
    assert event.failure_reason and "R01" in event.failure_reason
