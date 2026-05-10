"""Unit tests for `services.card_issuance.issue_card_for_invoice`.

The helper lives between the payment-run executor and the card-adapter
dispatcher. The tests below pin its three failure shapes (cards-not-
enabled, adapter raises, adapter returns failure) and the happy-path
mapping from adapter result onto a fresh `VirtualCard` row.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _invoice():
    return SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        vendor_name="Acme Supplies",
        amount=Decimal("1234.56"),
        currency="USD",
        description="Q1 office supplies",
        invoice_number="INV-42",
    )


def _app_settings():
    return SimpleNamespace(
        lithic_api_key="lithic-k",
        lithic_sandbox=True,
        nium_client_id="nium-c",
        nium_client_secret="nium-s",
        nium_customer_hash_id="cust",
        nium_wallet_hash_id="wallet",
        nium_sandbox=True,
    )


@pytest.mark.asyncio
async def test_issue_card_returns_failure_when_org_has_not_enabled_cards():
    """A row whose method is `virtual_card` but whose org never turned
    on the card program shouldn't crash the run — the executor wants
    a structured "skip this row" signal."""
    from app.services.card_issuance import issue_card_for_invoice

    result = await issue_card_for_invoice(
        invoice=_invoice(),
        organization_id=uuid.uuid4(),
        org_settings={"cards": {"enabled": False}},
        app_settings=_app_settings(),
    )

    assert result.success is False
    assert result.card is None
    assert result.failure_reason == "cards_not_enabled"


@pytest.mark.asyncio
async def test_issue_card_returns_failure_when_settings_missing_cards_key():
    """`org_settings={}` is the same business case as `cards.enabled=False`
    — never enabled. Belt-and-braces because callers can hand us None too."""
    from app.services.card_issuance import issue_card_for_invoice

    result = await issue_card_for_invoice(
        invoice=_invoice(),
        organization_id=uuid.uuid4(),
        org_settings={},
        app_settings=_app_settings(),
    )

    assert result.success is False
    assert result.failure_reason == "cards_not_enabled"


@pytest.mark.asyncio
async def test_issue_card_swallows_adapter_exceptions_as_structured_failure():
    """A flaky card provider must not raise out of the executor — the
    row should be skipped with `adapter_error:<ClassName>` so the run
    can finish and the operator can chase the rest by hand."""
    from app.services.card_issuance import issue_card_for_invoice

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(side_effect=RuntimeError("503 from upstream"))

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=_invoice(),
            organization_id=uuid.uuid4(),
            org_settings={"cards": {"enabled": True, "program_type": "platform", "region": "US"}},
            app_settings=_app_settings(),
        )

    assert result.success is False
    assert result.card is None
    assert result.failure_reason == "adapter_error:RuntimeError"


@pytest.mark.asyncio
async def test_issue_card_returns_failure_reason_from_adapter():
    """The adapter is the source of truth for business-level failures
    (e.g. KYC reject). The helper forwards `result.failure_reason` so
    the audit row is informative."""
    from app.services.card_issuance import issue_card_for_invoice

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(
        return_value=SimpleNamespace(
            success=False,
            failure_reason="insufficient_funds",
            provider_card_id=None,
            last_four=None,
        )
    )

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=_invoice(),
            organization_id=uuid.uuid4(),
            org_settings={"cards": {"enabled": True, "program_type": "platform", "region": "US"}},
            app_settings=_app_settings(),
        )

    assert result.success is False
    assert result.failure_reason == "insufficient_funds"


@pytest.mark.asyncio
async def test_issue_card_happy_path_builds_virtual_card_row():
    """On adapter success, the helper hands back an uncommitted
    `VirtualCard` row stamped with the provider's identifiers and the
    invoice's correlation id (needed for webhook reconciliation)."""
    from app.services.card_issuance import issue_card_for_invoice

    inv = _invoice()
    payment_id = uuid.uuid4()
    org_id = uuid.uuid4()

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            failure_reason=None,
            provider_card_id="card_abc123",
            last_four="4242",
        )
    )

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=org_id,
            org_settings={"cards": {"enabled": True, "program_type": "platform", "region": "US"}},
            app_settings=_app_settings(),
            payment_id=payment_id,
        )

    assert result.success is True
    assert result.card is not None
    card = result.card
    assert card.invoice_id == inv.id
    assert card.payment_id == payment_id
    assert card.organization_id == org_id
    assert card.correlation_id == inv.correlation_id
    assert card.card_provider == "lithic"
    assert card.provider_card_id == "card_abc123"
    assert card.last_four == "4242"
    assert card.amount_limit == inv.amount
    assert card.currency == "USD"
    assert card.status == "created"


@pytest.mark.asyncio
async def test_issue_card_uses_explicit_amount_when_provided():
    """The executor passes an explicit `amount` when the payment row
    differs from the invoice total (e.g., early-pay discount). The
    helper must forward that, not the invoice's gross."""
    from app.services.card_issuance import issue_card_for_invoice

    inv = _invoice()
    explicit_amount = Decimal("999.00")

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    captured_payload = {}

    async def capture(payload):
        captured_payload["amount"] = payload.amount
        return SimpleNamespace(
            success=True,
            failure_reason=None,
            provider_card_id="card_x",
            last_four="0000",
        )

    adapter.create_card = AsyncMock(side_effect=capture)

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=uuid.uuid4(),
            org_settings={"cards": {"enabled": True, "program_type": "platform", "region": "US"}},
            app_settings=_app_settings(),
            amount=explicit_amount,
        )

    assert captured_payload["amount"] == explicit_amount
    assert result.card is not None
    assert result.card.amount_limit == explicit_amount
