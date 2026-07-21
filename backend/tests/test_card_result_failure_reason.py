"""Regression: `CardResult` must carry a `failure_reason` field.

`card_issuance.issue_card_for_invoice` reads `result.failure_reason` when an
adapter returns `success=False`. The dataclass shipped without the field, so a
REAL adapter returning a failure raised `AttributeError` — swallowed by the
issuer's `except` and reported as `adapter_error:AttributeError`, masking every
provider business reason (insufficient_funds, kyc_blocked, …) as an internal
error. The existing issuance tests used `SimpleNamespace(...)` mocks that
happened to define the attribute, so they never caught this — these use the
concrete `CardResult`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.card_adapters.base import CardResult


def _db(existing_cards: int = 0):
    """Minimal AsyncSession stand-in.

    `issue_card_for_invoice` reads one thing off the session: how many
    VirtualCard rows the invoice already has (the re-issue discriminator in the
    provider idempotency key).
    """
    result = MagicMock()
    result.scalar_one = MagicMock(return_value=existing_cards)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def test_card_result_has_failure_reason_field_defaulting_none():
    # A success result never sets it — must default to None, not raise.
    ok = CardResult(success=True, provider_card_id="card_x", last_four="4242")
    assert ok.failure_reason is None

    # A failure result can carry the provider's machine-readable reason.
    bad = CardResult(success=False, failure_reason="insufficient_funds")
    assert bad.failure_reason == "insufficient_funds"


@pytest.mark.asyncio
async def test_issue_card_forwards_failure_reason_from_a_real_cardresult():
    """The path the SimpleNamespace mocks hid: a concrete `CardResult`
    failure must flow through the issuer as the real reason, not an
    AttributeError-masked `adapter_error:*`."""
    from app.services.card_issuance import issue_card_for_invoice

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(
        return_value=CardResult(success=False, failure_reason="kyc_blocked")
    )

    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        vendor_name="Acme",
        amount=Decimal("100.00"),
        currency="USD",
        description="x",
        invoice_number="INV-1",
    )
    app_settings = SimpleNamespace(
        lithic_api_key="k",
        lithic_sandbox=True,
        nium_client_id="c",
        nium_client_secret="s",
        nium_customer_hash_id="h",
        nium_wallet_hash_id="w",
        nium_sandbox=True,
    )

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            db=_db(),
            invoice=invoice,
            organization_id=uuid.uuid4(),
            org_settings={"cards": {"enabled": True, "program_type": "platform", "region": "US"}},
            app_settings=app_settings,
        )

    assert result.success is False
    assert result.failure_reason == "kyc_blocked"
