"""Live integration test for the stripe_treasury adapter against stripe-mock.

Exercises the real adapter (test_connection, create_payment, get_payment_status)
over HTTP against Stripe's official API mock, validating request shape +
response parsing without a live Stripe account.

Gated: the module is skipped unless FEOH_STRIPE_API_BASE is set AND stripe-mock
answers — so it runs locally after `pnpm stripe:up` (with
FEOH_STRIPE_API_BASE=http://localhost:12111/v1) and in the CI e2e job, and is a
no-op otherwise. stripe-mock returns canned fixtures (no persisted state), so the
assertions check the adapter's success/parse path, not stateful flows.

Run locally:
    pnpm stripe:up
    FEOH_STRIPE_API_BASE=http://localhost:12111/v1 pytest tests/test_stripe_mock_integration.py -v
"""

from __future__ import annotations

import os
from decimal import Decimal

import httpx
import pytest

API_BASE = os.environ.get("FEOH_STRIPE_API_BASE", "")


def _stripe_mock_up() -> bool:
    if not API_BASE:
        return False
    try:
        # Any HTTP response (even 4xx) means stripe-mock is listening.
        httpx.get(
            f"{API_BASE}/treasury/financial_accounts",
            headers={"Authorization": "Bearer sk_test_x"},
            timeout=2.0,
        )
        return True
    except httpx.HTTPError:
        return False


_UP = _stripe_mock_up()

# Locally this module skips when stripe-mock isn't up, so a dev box without
# `pnpm stripe:up` still runs the rest of the suite. But the CI service-e2e
# job starts stripe-mock on purpose and sets FEOH_REQUIRE_INTEGRATION — there,
# an unreachable service is a hard failure, never a silent skip that leaves
# the job green with this coverage quietly dropped.
if not _UP and os.environ.get("FEOH_REQUIRE_INTEGRATION"):
    raise RuntimeError(
        "stripe-mock is required (FEOH_REQUIRE_INTEGRATION is set) but was not "
        "reachable at FEOH_STRIPE_API_BASE. The CI service-e2e job starts it "
        "on purpose; refusing to skip and drop coverage silently."
    )

pytestmark = [
    pytest.mark.skipif(
        not _UP,
        reason="stripe-mock not configured/reachable — set FEOH_STRIPE_API_BASE + `pnpm stripe:up`",
    ),
    pytest.mark.asyncio,
]


def _adapter():
    from app.services.payment_adapters.stripe_treasury import StripeTreasuryAdapter

    return StripeTreasuryAdapter({"api_key": "sk_test_x", "financial_account_id": "fa_123"})


def _payload():
    from app.services.payment_adapters.base import PaymentPayload

    return PaymentPayload(
        correlation_id="corr_e2e_1",
        invoice_id="inv_e2e_1",
        invoice_number="INV-E2E-1",
        vendor_name="Acme Supplies",
        amount=Decimal("100.00"),
        currency="USD",
        method="ach",
        description="stripe-mock integration",
        vendor_bank={"counterparty_id": "ba_123"},
        metadata={},
    )


async def test_adapter_targets_stripe_mock():
    adapter = _adapter()
    assert adapter.api_base == API_BASE


async def test_test_connection_succeeds():
    assert await _adapter().test_connection() is True


async def test_create_payment_round_trips():
    from app.services.payment_adapters.base import PaymentStatus

    adapter = _adapter()
    result = await adapter.create_payment(_payload())
    assert result.success is True
    assert result.provider_payment_id
    assert isinstance(result.status, PaymentStatus)


async def test_get_payment_status_parses():
    from app.services.payment_adapters.base import PaymentStatus

    adapter = _adapter()
    created = await adapter.create_payment(_payload())
    status = await adapter.get_payment_status(created.provider_payment_id)
    assert isinstance(status, PaymentStatus)
