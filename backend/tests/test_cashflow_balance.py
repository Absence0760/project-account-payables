"""Unit tests for the cash-position bank-balance auto-sync + persisted
thresholds helpers (`app/services/cashflow.py`) and the payment-adapter
`get_balance` capability (`payment_adapters/base.py` + `mock_adapter.py`).

Pure / in-process — no DB, no network. The API-layer wiring (cash_position
auto-seed + threshold round-trip + RBAC) is covered in
`test_cashflow_forecast_api.py`.
"""

from __future__ import annotations

from decimal import Decimal

from app.services.cashflow import (
    CashThresholds,
    fetch_provider_balance,
    resolve_cash_thresholds,
    store_cash_thresholds,
)
from app.services.payment_adapters import (
    BalanceResult,
    PaymentAdapter,
    get_payment_adapter,
)
from app.services.payment_adapters.dispatcher import register_payment_adapter

# ---------------------------------------------------------------------------
# Adapter get_balance capability
# ---------------------------------------------------------------------------


async def test_base_adapter_balance_unsupported_by_default():
    """An adapter that doesn't override `get_balance` reports unavailable —
    existing adapters are unaffected by the new capability."""

    class _Bare(PaymentAdapter):
        provider_name = "bare"

    result = await _Bare({}).get_balance()
    assert result.available is False
    assert result.unavailable_reason == "not_supported"
    assert isinstance(result.amount, Decimal)


async def test_mock_adapter_returns_deterministic_balance():
    """The local-first mock returns a deterministic Decimal balance so dev
    needs no real bank credential."""
    adapter = get_payment_adapter({"provider": "mock"})
    result = await adapter.get_balance()
    assert result.available is True
    assert result.amount == Decimal("250000.00")
    assert result.currency == "USD"
    # Account label is opaque — never a full account number.
    assert result.account_ref == "mock-operating"


async def test_mock_adapter_balance_config_override():
    adapter = get_payment_adapter(
        {"provider": "mock", "balance": "1234.56", "balance_currency": "EUR"}
    )
    result = await adapter.get_balance()
    assert result.amount == Decimal("1234.56")
    assert result.currency == "EUR"


async def test_mock_adapter_balance_can_simulate_unsupported():
    adapter = get_payment_adapter({"provider": "mock", "balance_available": False})
    result = await adapter.get_balance()
    assert result.available is False


# ---------------------------------------------------------------------------
# fetch_provider_balance — best-effort, fails soft
# ---------------------------------------------------------------------------


async def test_fetch_provider_balance_from_mock():
    pb = await fetch_provider_balance({"provider": "mock"})
    assert pb is not None
    assert pb.amount == Decimal("250000.00")
    assert pb.provider == "mock"
    assert pb.currency == "USD"


async def test_fetch_provider_balance_none_when_unsupported():
    """An adapter whose get_balance is unavailable → None (caller falls back to
    the manual opening balance)."""
    pb = await fetch_provider_balance({"provider": "mock", "balance_available": False})
    assert pb is None


async def test_fetch_provider_balance_no_config_resolves_to_mock():
    """The helper itself resolves None config to the mock adapter (deterministic).
    The cash_position route gates the call on a configured provider so a bare
    clone doesn't fabricate a balance — see test_cash_position_* in
    test_cashflow_forecast_api.py."""
    pb = await fetch_provider_balance(None)
    assert pb is not None
    assert pb.amount == Decimal("250000.00")


async def test_fetch_provider_balance_swallows_adapter_error():
    """A provider that raises must not propagate — the dashboard falls back to
    the manual opening balance instead of 500-ing."""

    @register_payment_adapter("explodes_on_balance")
    class _Exploder(PaymentAdapter):
        provider_name = "explodes_on_balance"

        async def get_balance(self) -> BalanceResult:
            raise RuntimeError("bank link down")

    pb = await fetch_provider_balance({"provider": "explodes_on_balance"})
    assert pb is None


# ---------------------------------------------------------------------------
# Persisted thresholds — resolve / store round-trip
# ---------------------------------------------------------------------------


def test_resolve_thresholds_missing_block():
    assert resolve_cash_thresholds(None).min_balance_threshold is None
    assert resolve_cash_thresholds({}).min_balance_threshold is None
    assert resolve_cash_thresholds({"cashflow": "nonsense"}).min_balance_threshold is None


def test_resolve_thresholds_reads_decimal():
    t = resolve_cash_thresholds({"cashflow": {"min_balance_threshold": "5000.00"}})
    assert t.min_balance_threshold == Decimal("5000.00")


def test_resolve_thresholds_tolerates_garbage_value():
    """A corrupt stored value must not break the read."""
    t = resolve_cash_thresholds({"cashflow": {"min_balance_threshold": "not-a-number"}})
    assert t.min_balance_threshold is None


def test_store_thresholds_round_trips_as_string():
    settings = store_cash_thresholds(None, CashThresholds(min_balance_threshold=Decimal("750.50")))
    # Stored as a string (money never round-trips through float in JSON).
    assert settings["cashflow"]["min_balance_threshold"] == "750.50"
    assert resolve_cash_thresholds(settings).min_balance_threshold == Decimal("750.50")


def test_store_thresholds_preserves_other_cashflow_keys():
    """Setting a threshold must not clobber a manually set opening_balance."""
    existing = {"cashflow": {"opening_balance": "10000"}, "brand": {"product_name": "X"}}
    out = store_cash_thresholds(existing, CashThresholds(min_balance_threshold=Decimal("1")))
    assert out["cashflow"]["opening_balance"] == "10000"
    assert out["cashflow"]["min_balance_threshold"] == "1"
    assert out["brand"]["product_name"] == "X"
    # Input not mutated in place.
    assert "min_balance_threshold" not in existing["cashflow"]


def test_store_thresholds_none_clears_key():
    existing = {"cashflow": {"min_balance_threshold": "500"}}
    out = store_cash_thresholds(existing, CashThresholds(min_balance_threshold=None))
    assert "min_balance_threshold" not in out["cashflow"]
