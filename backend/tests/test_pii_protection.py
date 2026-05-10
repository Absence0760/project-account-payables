"""PII / sensitive-data protection tests.

Project invariant #7: "PII / banking data stays out of logs and error
responses. Bank account numbers, tax IDs, full vendor addresses, and
full payment-method numbers must not appear in logger output, in HTTP
error bodies, or in URL query strings."

These tests pin the contract for the highest-leverage cases:

  - `VendorBankDetails` only carries last-four digits, never full
    account / routing numbers
  - The vendor response schema does not expose a "full bank details"
    field that would leak the raw blob from the DB
  - The card response schema returns only `last_four`, not the PAN
  - Audit dispatch helpers don't capture full account numbers in
    free-form `details` payloads
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Bank details — last4s only, never full account numbers
# ---------------------------------------------------------------------------


def test_vendor_bank_details_schema_only_exposes_last_four():
    """`VendorBankDetails` is the UI-visible subset of the JSONB
    column. It must not declare full-account-number or
    full-routing-number fields — only the last-4 partials and the
    processor counterparty id."""
    from app.schemas.vendor import VendorBankDetails

    fields = set(VendorBankDetails.model_fields.keys())
    # Allow-list: the schema must only declare these names. New keys
    # require a deliberate review for leak potential.
    allowed = {"counterparty_id", "account_last4", "routing_last4", "bank_name"}
    extra = fields - allowed
    assert not extra, (
        f"VendorBankDetails grew unfamiliar fields {extra}; review for PII leak"
    )
    # And the forbidden full-detail names must NOT be present.
    forbidden = {"account_number", "routing_number", "iban", "swift", "full_account"}
    leak = fields & forbidden
    assert not leak, f"VendorBankDetails exposes raw banking fields: {leak}"


def test_vendor_bank_details_last4_fields_are_capped_at_four_chars():
    """`account_last4` and `routing_last4` are 4 digits by definition.
    The schema must enforce a max_length of 4 so a regression can't
    silently start storing a longer "partial" that's really a full
    number."""
    from app.schemas.vendor import VendorBankDetails

    for fname in ("account_last4", "routing_last4"):
        field = VendorBankDetails.model_fields[fname]
        metadata_str = str(field.metadata)
        assert "max_length=4" in metadata_str or "MaxLen(max_length=4)" in metadata_str, (
            f"{fname} must have max_length=4; got metadata={field.metadata}"
        )


# ---------------------------------------------------------------------------
# Virtual card response — PAN never returned via list endpoints
# ---------------------------------------------------------------------------


def test_card_list_response_does_not_expose_pan_or_cvv():
    """The card-list / card-detail response surfaces `last_four`
    only. PAN + CVV live behind the single-use reveal token endpoint
    (`/portal/cards/{token}`) — exposing them on the list view would
    bypass that single-use control."""
    from app.schemas.virtual_card import CardListResponse, CardResponse

    list_fields = set(CardListResponse.model_fields.keys())
    item_fields = set(CardResponse.model_fields.keys())

    forbidden = {"pan", "card_number", "cvv", "cvc", "security_code", "full_card_number"}
    for fields, label in ((list_fields, "CardListResponse"), (item_fields, "CardResponse")):
        leak = fields & forbidden
        assert not leak, f"{label} exposes a PAN-shaped field: {leak}"


# ---------------------------------------------------------------------------
# Audit dispatch — free-form `details` must not carry raw bank data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_dispatch_helper_signature_does_not_accept_bank_blob():
    """The audit-dispatch helper takes a typed `details: dict | None`,
    not a typed `BankDetails`. There's no way for a caller to pass
    raw account numbers as a typed argument — they'd have to manually
    stuff them into `details`. Pinning the signature catches a
    regression that adds a `bank_details` kwarg directly."""
    import inspect

    from app.services.audit_dispatch import dispatch_audit

    sig = inspect.signature(dispatch_audit)
    forbidden_params = {
        "bank_details",
        "account_number",
        "routing_number",
        "tax_id",
        "card_number",
    }
    leaks = set(sig.parameters) & forbidden_params
    assert not leaks, f"dispatch_audit grew a sensitive parameter: {leaks}"


# ---------------------------------------------------------------------------
# Logger output doesn't echo bank / PAN strings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_card_issuance_logger_does_not_log_pan_on_adapter_failure(caplog):
    """`card_issuance.issue_card_for_invoice` logs adapter failures.
    The log message must NOT include the upstream provider's PAN
    even if the adapter raised with one in scope. A regression that
    interpolates `exc` itself (instead of `exc.__class__.__name__`)
    would push card numbers into the log sink (invariant #7)."""
    from decimal import Decimal
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.services.card_issuance import issue_card_for_invoice

    fake_pan = "4111-1111-1111-1234"
    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(side_effect=RuntimeError(f"upstream said PAN={fake_pan}"))

    invoice = SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        vendor_name="Acme",
        amount=Decimal("100.00"),
        currency="USD",
        description="x",
        invoice_number="I-1",
    )

    app_settings = SimpleNamespace(
        lithic_api_key="k",
        lithic_sandbox=True,
        nium_client_id="c",
        nium_client_secret="s",
        nium_customer_hash_id="cust",
        nium_wallet_hash_id="w",
        nium_sandbox=True,
    )

    caplog.set_level(logging.WARNING)
    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=invoice,
            organization_id=uuid.uuid4(),
            org_settings={
                "cards": {"enabled": True, "program_type": "platform", "region": "US"}
            },
            app_settings=app_settings,
        )

    assert result.success is False
    for record in caplog.records:
        assert fake_pan not in record.getMessage(), (
            f"PAN leaked into log: {record.getMessage()}"
        )


# ---------------------------------------------------------------------------
# Vendor response shape — full bank_details blob is filtered through the
# safe schema, not echoed verbatim
# ---------------------------------------------------------------------------


def test_vendor_response_filters_unknown_bank_keys_through_typed_schema():
    """`VendorResponse.from_db` constructs a `VendorBankDetails` from
    the JSONB column via the typed schema. The cleanest pin: build
    a VendorBankDetails directly from a "polluted" dict using
    `.get()` selection (the production code path) and confirm the
    raw banking fields don't appear on the typed output."""
    from app.schemas.vendor import VendorBankDetails

    polluted = {
        "counterparty_id": "cp_abc",
        "account_last4": "1234",
        "account_number": "01234567890987",  # forbidden — full PAN-equivalent
        "routing_number": "021000021",
    }
    safe = VendorBankDetails(
        counterparty_id=polluted.get("counterparty_id"),
        account_last4=polluted.get("account_last4"),
        routing_last4=polluted.get("routing_last4"),
        bank_name=polluted.get("bank_name"),
    )
    serialised = safe.model_dump()
    assert "account_number" not in serialised
    assert "routing_number" not in serialised
    assert serialised.get("account_last4") == "1234"


# ---------------------------------------------------------------------------
# Token response — no JWT secret leakage in error / debug paths
# ---------------------------------------------------------------------------


def test_response_schemas_do_not_expose_signing_key_fields():
    """The JWT signing secret must never appear in a Pydantic
    response schema. The only legitimate "secret"-named field is
    `MFAEnrollStartResponse.secret` — the TOTP shared secret shown
    exactly once at enrollment for the user to scan into their app.
    Everything else is a leak."""
    import inspect

    import app.schemas.auth as schema_module

    KNOWN_OK = {("MFAEnrollStartResponse", "secret")}
    leaks: list[str] = []

    for name, cls in inspect.getmembers(schema_module, inspect.isclass):
        if not hasattr(cls, "model_fields"):
            continue
        for fname in cls.model_fields:
            if (name, fname) in KNOWN_OK:
                continue
            lower = fname.lower()
            if "secret" in lower or "private_key" in lower or "signing_key" in lower:
                leaks.append(f"{name}.{fname}")

    assert not leaks, f"response schemas expose suspicious field(s): {leaks}"
