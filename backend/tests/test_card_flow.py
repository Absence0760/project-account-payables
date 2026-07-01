"""Virtual-card issuance flow — pins the behavior of
`issue_card_for_invoice` and the courtesy `notify_vendor_of_card`.

The card path is dual-entry: the explicit `/api/cards/generate`
endpoint, and the `execute_payment_run` executor when a payment's
method is `virtual_card`. Both routes go through this module. A
regression on the failure-reason taxonomy (`cards_not_enabled`,
`adapter_error:*`, `adapter_returned_failure`) silently changes what
shows up on the failed-payment row and what the AP team sees in the
exception queue.

These tests pin:
  - `cards.enabled=False` short-circuits with `cards_not_enabled`,
    never invokes an adapter.
  - Adapter raises → result carries `adapter_error:<ExceptionClass>`.
    Crucially, the exception *message* is NOT in the log line (PII
    leak invariant #7); only the class name.
  - Adapter returns `success=False` → result mirrors the
    `failure_reason` from the adapter, with a generic fallback.
  - Adapter returns `success=True` → a `VirtualCard` row is built
    with the right amount limit, expiry, provider, and links to
    invoice + payment + vendor.
  - `notify_vendor_of_card` fail-soft semantics: missing vendor,
    missing email, missing URL template all return False without
    raising.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.card_issuance import (
    DEFAULT_CARD_EXPIRY_DAYS,
    _coerce_expiry_days,
    _resolve_card_config,
    issue_card_for_invoice,
    notify_vendor_of_card,
)


def _invoice(**overrides):
    base = dict(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        vendor_name="Acme Corp",
        amount=Decimal("250.00"),
        currency="USD",
        description="Pro services",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _app_settings():
    """Minimal app_settings-like object; not actually exercised once we
    short-circuit on cards_not_enabled."""
    return SimpleNamespace(
        lithic_api_key="x",
        lithic_sandbox=True,
        nium_client_id="x",
        nium_client_secret="x",
        nium_customer_hash_id="x",
        nium_wallet_hash_id="x",
        nium_sandbox=True,
    )


# ---------------------------------------------------------------------------
# issue_card_for_invoice — short-circuit when cards aren't enabled.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_card_short_circuits_when_cards_not_enabled():
    """An org without `cards.enabled=True` MUST NOT trigger the adapter
    dispatcher (which would try to read credentials). Verify the
    short-circuit by asserting `get_card_adapter` is never called."""
    inv = _invoice()
    org_settings: dict = {}  # no cards key

    with patch("app.services.card_adapters.get_card_adapter") as mk_adapter:
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=uuid.uuid4(),
            org_settings=org_settings,
            app_settings=_app_settings(),
        )

    assert result.success is False
    assert result.card is None
    assert result.failure_reason == "cards_not_enabled"
    mk_adapter.assert_not_called()


@pytest.mark.asyncio
async def test_issue_card_short_circuits_when_cards_disabled_explicitly():
    """`cards.enabled=False` (set by an admin to turn off the feature)
    also short-circuits. Different code path from "key not set"."""
    inv = _invoice()
    org_settings = {"cards": {"enabled": False, "program_type": "platform"}}

    with patch("app.services.card_adapters.get_card_adapter") as mk_adapter:
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=uuid.uuid4(),
            org_settings=org_settings,
            app_settings=_app_settings(),
        )

    assert result.failure_reason == "cards_not_enabled"
    mk_adapter.assert_not_called()


# ---------------------------------------------------------------------------
# issue_card_for_invoice — adapter raises.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_card_returns_adapter_error_when_adapter_raises():
    """Adapter raises an exception → result is success=False with a
    failure_reason of `adapter_error:<ClassName>`. The exception's
    *message* is never returned (could leak provider PAN/token)."""
    inv = _invoice()
    org_settings = {"cards": {"enabled": True, "program_type": "platform"}}

    class ProviderTimeout(Exception):
        pass

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(side_effect=ProviderTimeout("PAN 4111111111111111 timed out"))

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=uuid.uuid4(),
            org_settings=org_settings,
            app_settings=_app_settings(),
        )

    assert result.success is False
    assert result.card is None
    assert result.failure_reason == "adapter_error:ProviderTimeout"
    # Class name MUST be in the failure_reason; the actual PAN-containing
    # message MUST NOT be.
    assert "4111111111111111" not in (result.failure_reason or "")


# ---------------------------------------------------------------------------
# issue_card_for_invoice — adapter returns failure.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_card_propagates_adapter_failure_reason():
    """Adapter returns `success=False, failure_reason="insufficient_funds"`
    → that reason flows through unchanged. The fallback only kicks in
    when the adapter omitted a reason."""
    inv = _invoice()
    org_settings = {"cards": {"enabled": True, "program_type": "platform"}}

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
            invoice=inv,
            organization_id=uuid.uuid4(),
            org_settings=org_settings,
            app_settings=_app_settings(),
        )

    assert result.failure_reason == "insufficient_funds"


@pytest.mark.asyncio
async def test_issue_card_uses_generic_failure_when_adapter_omits_reason():
    """A misbehaving adapter that returned success=False but no reason
    must still produce a meaningful failure_reason — don't surface a
    null."""
    inv = _invoice()
    org_settings = {"cards": {"enabled": True, "program_type": "platform"}}

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(
        return_value=SimpleNamespace(
            success=False, failure_reason=None, provider_card_id=None, last_four=None
        )
    )

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=uuid.uuid4(),
            org_settings=org_settings,
            app_settings=_app_settings(),
        )

    assert result.failure_reason == "adapter_returned_failure"


# ---------------------------------------------------------------------------
# issue_card_for_invoice — happy path builds a row with the right shape.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_issue_card_builds_virtual_card_row_on_success():
    """Adapter says success → we build a VirtualCard with the right
    amount_limit, provider, last_four, link back to invoice/payment/
    vendor, and `status="created"`. Money-typed amount MUST be the
    Decimal we passed in, not a float."""
    inv = _invoice(amount=Decimal("250.00"))
    pid = uuid.uuid4()
    org_id = uuid.uuid4()
    org_settings = {
        "cards": {
            "enabled": True,
            "program_type": "platform",
            "default_expiry_days": 14,
        }
    }

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(
        return_value=SimpleNamespace(
            success=True, failure_reason=None, provider_card_id="card_abc", last_four="4242"
        )
    )

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=org_id,
            org_settings=org_settings,
            app_settings=_app_settings(),
            payment_id=pid,
            amount=Decimal("250.00"),
        )

    assert result.success is True
    card = result.card
    assert card is not None
    assert card.invoice_id == inv.id
    assert card.payment_id == pid
    assert card.vendor_id == inv.vendor_id
    assert card.card_provider == "lithic"
    assert card.provider_card_id == "card_abc"
    assert card.last_four == "4242"
    assert card.amount_limit == Decimal("250.00")
    assert isinstance(card.amount_limit, Decimal), (
        "amount_limit must be Decimal — invariant #1 (money is exact)"
    )
    assert card.currency == "USD"
    assert card.status == "created"
    assert card.organization_id == org_id
    # Adapter must have been called exactly once with a payload
    # carrying the invoice/vendor/amount.
    adapter.create_card.assert_awaited_once()
    payload = adapter.create_card.call_args.args[0]
    assert payload.invoice_id == str(inv.id)
    assert payload.amount == Decimal("250.00")
    assert payload.expiry_days == 14  # org override propagates end-to-end


@pytest.mark.asyncio
async def test_issue_card_defaults_expiry_to_30_days_when_unset():
    """Org doesn't override `default_expiry_days` → the helper uses 30."""
    inv = _invoice()
    org_settings = {"cards": {"enabled": True, "program_type": "platform"}}

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(
        return_value=SimpleNamespace(
            success=True, failure_reason=None, provider_card_id="x", last_four="0000"
        )
    )

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=uuid.uuid4(),
            org_settings=org_settings,
            app_settings=_app_settings(),
        )

    payload = adapter.create_card.call_args.args[0]
    assert payload.expiry_days == 30
    # The model's expires_at is `now() + 30 days`, but we already pinned
    # the payload — that's the load-bearing wire-up.
    assert result.card.expires_at is not None


# ---------------------------------------------------------------------------
# notify_vendor_of_card — fail-soft branches.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _coerce_expiry_days — input sanitization. JSONB settings can carry
# anything; we must never mint a card whose expiry came from garbage.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, DEFAULT_CARD_EXPIRY_DAYS),
        (14, 14),
        ("14", 14),  # form input arrives as a string
        ("  21  ", 21),  # int() tolerates surrounding whitespace
        (60.0, 60),  # float from old JSON
        (0, DEFAULT_CARD_EXPIRY_DAYS),  # zero is rejected
        (-5, DEFAULT_CARD_EXPIRY_DAYS),  # negative is rejected
        ("abc", DEFAULT_CARD_EXPIRY_DAYS),  # non-numeric falls back
        ("", DEFAULT_CARD_EXPIRY_DAYS),
        ([1, 2], DEFAULT_CARD_EXPIRY_DAYS),  # weird shapes fall back
    ],
)
def test_coerce_expiry_days_normalises_garbage_to_default(raw, expected):
    """Org settings are JSONB and ultimately admin-editable. Any value
    that can't reduce to a positive int falls back to the platform
    default. A regression here either mints cards with bad expiries
    (negative, zero, NaN) or raises mid-issuance, blocking payment."""
    assert _coerce_expiry_days(raw) == expected


# ---------------------------------------------------------------------------
# _resolve_card_config — propagates default_expiry_days through every
# branch (platform/lithic, platform/nium, byok).
# ---------------------------------------------------------------------------


def test_resolve_card_config_returns_none_when_cards_disabled():
    """No `cards.enabled=True` → resolver short-circuits with None.
    The issuer uses this as its own short-circuit; pin it so a
    regression to `{}` doesn't silently flow into adapter dispatch."""
    assert _resolve_card_config({}, _app_settings()) is None
    assert _resolve_card_config({"cards": {"enabled": False}}, _app_settings()) is None


def test_resolve_card_config_platform_defaults_expiry_when_unset():
    """Platform program, no override → resolved config carries the
    platform default. Was a bug: the key wasn't in the resolved dict
    at all, so downstream `config.get("default_expiry_days", 30)`
    always returned 30 — making the setting cosmetic."""
    cfg = _resolve_card_config(
        {"cards": {"enabled": True, "program_type": "platform"}},
        _app_settings(),
    )
    assert cfg is not None
    assert cfg["default_expiry_days"] == DEFAULT_CARD_EXPIRY_DAYS


def test_resolve_card_config_platform_honors_expiry_override():
    """Platform program with `default_expiry_days=7` → resolved config
    carries 7. This is the load-bearing assertion for the fix."""
    cfg = _resolve_card_config(
        {"cards": {"enabled": True, "program_type": "platform", "default_expiry_days": 7}},
        _app_settings(),
    )
    assert cfg is not None
    assert cfg["default_expiry_days"] == 7


def test_resolve_card_config_byok_honors_expiry_override():
    """BYOK orgs supply their own keys AND can set their own expiry
    window. Both must propagate."""
    cfg = _resolve_card_config(
        {
            "cards": {
                "enabled": True,
                "program_type": "byok",
                "provider": "lithic",
                "api_key": "byok-key",
                "default_expiry_days": 45,
            }
        },
        _app_settings(),
    )
    assert cfg is not None
    assert cfg["default_expiry_days"] == 45
    assert cfg["provider"] == "lithic"
    assert cfg["api_key"] == "byok-key"
    # BYOK sandbox is opt-IN: an org supplying its own real keys and omitting
    # `sandbox` must get LIVE rails, not a silent sandbox that pays into a void.
    assert cfg["sandbox"] is False


def test_resolve_card_config_byok_sandbox_is_explicit_opt_in():
    """`"sandbox": true` still routes to the provider's sandbox — the flag is
    honoured when set; only the DEFAULT flipped from sandbox to live."""
    live = _resolve_card_config(
        {"cards": {"enabled": True, "program_type": "byok", "provider": "lithic", "api_key": "k"}},
        _app_settings(),
    )
    assert live["sandbox"] is False
    sandboxed = _resolve_card_config(
        {
            "cards": {
                "enabled": True,
                "program_type": "byok",
                "provider": "lithic",
                "api_key": "k",
                "sandbox": True,
            }
        },
        _app_settings(),
    )
    assert sandboxed["sandbox"] is True


def test_resolve_card_config_garbage_expiry_value_falls_back():
    """An admin saves "thirty" in the textbox → resolver MUST NOT
    crash and MUST NOT propagate the string into adapter land. Falls
    back to the default."""
    cfg = _resolve_card_config(
        {"cards": {"enabled": True, "program_type": "platform", "default_expiry_days": "thirty"}},
        _app_settings(),
    )
    assert cfg is not None
    assert cfg["default_expiry_days"] == DEFAULT_CARD_EXPIRY_DAYS


@pytest.mark.asyncio
async def test_issue_card_payload_carries_expiry_for_byok_override():
    """End-to-end: BYOK org with `default_expiry_days=60` → the payload
    sent to the card adapter declares 60. Pins the wire-up across the
    resolver/issuer/adapter boundary."""
    inv = _invoice()
    org_settings = {
        "cards": {
            "enabled": True,
            "program_type": "byok",
            "provider": "lithic",
            "api_key": "byok-key",
            "default_expiry_days": 60,
        }
    }

    adapter = MagicMock()
    adapter.provider_name = "lithic"
    adapter.create_card = AsyncMock(
        return_value=SimpleNamespace(
            success=True, failure_reason=None, provider_card_id="x", last_four="0000"
        )
    )

    with patch("app.services.card_adapters.get_card_adapter", return_value=adapter):
        result = await issue_card_for_invoice(
            invoice=inv,
            organization_id=uuid.uuid4(),
            org_settings=org_settings,
            app_settings=_app_settings(),
        )

    payload = adapter.create_card.call_args.args[0]
    assert payload.expiry_days == 60
    assert result.card.expires_at is not None


# ---------------------------------------------------------------------------
# notify_vendor_of_card — fail-soft branches.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_vendor_returns_false_when_invoice_has_no_vendor_id():
    """Invoice has no linked vendor → can't email anyone. Return
    False; do not raise, do not query the DB."""
    inv = SimpleNamespace(vendor_id=None, id=uuid.uuid4())
    card = SimpleNamespace(id=uuid.uuid4(), amount_limit=Decimal("100"))
    db = AsyncMock()
    sent = await notify_vendor_of_card(
        db,
        card=card,
        invoice=inv,
        org_name="Acme",
        org_slug="acme",
        public_url_template="https://{slug}.app.com",
    )
    assert sent is False
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_notify_vendor_returns_false_when_vendor_has_no_email():
    """Vendor row exists but `email` is None → skip silently. A
    regression that tried to send to None would crash the issuance
    path."""
    inv = SimpleNamespace(
        vendor_id=uuid.uuid4(), id=uuid.uuid4(), invoice_number="INV-1", currency="USD"
    )
    card = SimpleNamespace(id=uuid.uuid4(), amount_limit=Decimal("100"))
    vendor = SimpleNamespace(name="Acme", email=None)

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=vendor)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    sent = await notify_vendor_of_card(
        db,
        card=card,
        invoice=inv,
        org_name="Acme",
        org_slug="acme",
        public_url_template="https://{slug}.app.com",
    )
    assert sent is False


@pytest.mark.asyncio
async def test_notify_vendor_returns_false_when_no_url_template():
    """Without `AP_TENANT_URL_TEMPLATE` we have nowhere to point the
    vendor — skip the email rather than send a broken link."""
    inv = SimpleNamespace(
        vendor_id=uuid.uuid4(), id=uuid.uuid4(), invoice_number="INV-1", currency="USD"
    )
    card = SimpleNamespace(id=uuid.uuid4(), amount_limit=Decimal("100"))
    vendor = SimpleNamespace(name="Acme", email="ap@acme.com")

    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=vendor)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with patch(
        "app.services.card_reveal.mint_reveal_token",
        AsyncMock(return_value="tok"),
    ):
        sent = await notify_vendor_of_card(
            db,
            card=card,
            invoice=inv,
            org_name="Acme",
            org_slug="acme",
            public_url_template="",
        )
    assert sent is False
