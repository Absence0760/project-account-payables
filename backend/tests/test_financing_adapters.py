"""Supplier-financing adapter contract — dispatcher + mock + c2fo skeleton.

Pins:
  - dispatcher falls back to mock on empty / unknown config and
    resolves named providers
  - mock quote math: advance = face - fee, fee is Decimal-exact and
    proportional to days-to-due at the configured APR, repayment_date
    == invoice due_date
  - request_funding is idempotent: same key → same external id
  - the c2fo skeleton fails closed (raises) without an api_key, and WITH
    one answers in the contract's own vocabulary — an ineligible quote /
    an unfunded result — rather than raising `NotImplementedError` at the
    first caller. Its probe stays False either way.

Pure / async unit tests — adapters instantiated directly, no DB.
`asyncio_mode = "auto"` in pyproject means no explicit asyncio marker
is needed, but we keep them for parity with the sibling adapter tests.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.financing_adapters import (
    FinancingQuote,
    get_financing_adapter,
)
from app.services.financing_adapters.c2fo import REASON_NOT_IMPLEMENTED, C2FOAdapter
from app.services.financing_adapters.mock_adapter import MockFinancingAdapter

# A fixed funding date keeps every quote deterministic regardless of
# the wall clock — 90 days before the due date below.
_FUNDING = "2026-01-01"
_DUE = date(2026, 4, 1)  # 90 days after _FUNDING
_DAYS = (_DUE - date.fromisoformat(_FUNDING)).days  # 90


def _mock(**overrides) -> MockFinancingAdapter:
    cfg = {"mock_funding_date": _FUNDING, **overrides}
    return MockFinancingAdapter(cfg)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_dispatcher_falls_back_to_mock_on_empty_config():
    assert isinstance(get_financing_adapter(None), MockFinancingAdapter)
    assert isinstance(get_financing_adapter({}), MockFinancingAdapter)


def test_dispatcher_falls_back_to_mock_on_unknown_provider():
    adapter = get_financing_adapter({"provider": "no-such-financier"})
    assert isinstance(adapter, MockFinancingAdapter)


def test_dispatcher_resolves_named_providers():
    assert isinstance(get_financing_adapter({"provider": "mock"}), MockFinancingAdapter)
    assert isinstance(get_financing_adapter({"provider": "c2fo", "api_key": "k"}), C2FOAdapter)
    # Case-insensitive provider key.
    assert isinstance(get_financing_adapter({"provider": "C2FO"}), C2FOAdapter)


# ---------------------------------------------------------------------------
# Mock quote math
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_quote_advance_equals_amount_minus_fee():
    adapter = _mock()  # default 6% APR
    amount = Decimal("10000.00")
    quote = await adapter.quote(
        invoice_amount=amount,
        currency="USD",
        due_date=_DUE,
        vendor_name="Acme Supplies",
    )

    assert quote.eligible is True
    assert quote.provider == "mock"
    assert quote.repayment_date == _DUE
    assert quote.funding_date == date.fromisoformat(_FUNDING)
    # fee_percent = 6.0 * 90 / 365 = 1.4795...% → quantized to 4dp.
    expected_pct = (Decimal("6.0") * Decimal(_DAYS) / Decimal(365)).quantize(Decimal("0.0001"))
    assert quote.fee_percent == expected_pct
    assert quote.discount_percent == quote.fee_percent
    # fee_amount = amount * pct / 100, rounded to cents.
    expected_fee = (amount * expected_pct / Decimal(100)).quantize(Decimal("0.01"))
    assert quote.advance_amount == amount - expected_fee
    # Decimal-exact, not float.
    assert isinstance(quote.advance_amount, Decimal)
    assert isinstance(quote.fee_percent, Decimal)


@pytest.mark.asyncio
async def test_mock_fee_scales_with_days_to_due():
    adapter = _mock()
    near = await adapter.quote(
        invoice_amount=Decimal("10000.00"),
        currency="USD",
        due_date=date(2026, 1, 31),  # 30 days
        vendor_name="V",
    )
    far = await adapter.quote(
        invoice_amount=Decimal("10000.00"),
        currency="USD",
        due_date=date(2026, 7, 1),  # 181 days
        vendor_name="V",
    )
    # A longer acceleration window costs more.
    assert far.fee_percent > near.fee_percent
    assert far.advance_amount < near.advance_amount


@pytest.mark.asyncio
async def test_mock_quote_ineligible_when_due_date_not_in_future():
    adapter = _mock()
    quote = await adapter.quote(
        invoice_amount=Decimal("500.00"),
        currency="USD",
        due_date=date.fromisoformat(_FUNDING),  # due == funding day
        vendor_name="V",
    )
    assert quote.eligible is False
    assert quote.reason == "no_acceleration_window"
    assert quote.advance_amount == Decimal("0.00")
    assert quote.fee_percent == Decimal("0.00")


@pytest.mark.asyncio
async def test_mock_quote_ineligible_on_non_positive_amount():
    adapter = _mock()
    quote = await adapter.quote(
        invoice_amount=Decimal("0.00"),
        currency="USD",
        due_date=_DUE,
        vendor_name="V",
    )
    assert quote.eligible is False
    assert quote.reason == "invoice_amount_non_positive"


@pytest.mark.asyncio
async def test_mock_custom_annual_rate_override():
    cheap = _mock(mock_annual_rate_percent="3.0")
    pricey = _mock(mock_annual_rate_percent="12.0")
    q_cheap = await cheap.quote(
        invoice_amount=Decimal("10000.00"), currency="USD", due_date=_DUE, vendor_name="V"
    )
    q_pricey = await pricey.quote(
        invoice_amount=Decimal("10000.00"), currency="USD", due_date=_DUE, vendor_name="V"
    )
    # 4x the APR is ~4x the fee; allow a cent of independent 4dp
    # quantization slack on the ratio comparison.
    assert abs(q_pricey.fee_percent - q_cheap.fee_percent * Decimal(4)) <= Decimal("0.0002")
    assert q_pricey.fee_percent > q_cheap.fee_percent


# ---------------------------------------------------------------------------
# Mock funding determinism + fee recovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_request_funding_is_deterministic():
    adapter = _mock()
    quote = await adapter.quote(
        invoice_amount=Decimal("10000.00"),
        currency="USD",
        due_date=_DUE,
        vendor_name="V",
    )
    r1 = await adapter.request_funding(quote=quote, idempotency_key="invoice-42")
    r2 = await adapter.request_funding(quote=quote, idempotency_key="invoice-42")
    r3 = await adapter.request_funding(quote=quote, idempotency_key="invoice-99")

    assert r1.funded is True
    assert r1.external_funding_id == r2.external_funding_id  # same key → same id
    assert r1.external_funding_id != r3.external_funding_id  # different key → different id
    assert r1.external_funding_id.startswith("mock-fund-")
    assert r1.status == "funded"


@pytest.mark.asyncio
async def test_mock_funding_recovers_fee_amount_from_quote():
    adapter = _mock()
    amount = Decimal("10000.00")
    quote = await adapter.quote(
        invoice_amount=amount, currency="USD", due_date=_DUE, vendor_name="V"
    )
    result = await adapter.request_funding(quote=quote, idempotency_key="k")
    # advance + fee should reconstruct the face value (within a cent of
    # rounding); fee is Decimal.
    assert isinstance(result.fee_amount, Decimal)
    assert abs((result.advance_amount + result.fee_amount) - amount) <= Decimal("0.01")


@pytest.mark.asyncio
async def test_mock_funding_declines_ineligible_quote():
    adapter = _mock()
    bad = FinancingQuote(
        provider="mock",
        eligible=False,
        discount_percent=Decimal("0.00"),
        fee_percent=Decimal("0.00"),
        funding_date=None,
        repayment_date=None,
        advance_amount=Decimal("0.00"),
        reason="vendor_not_eligible",
    )
    result = await adapter.request_funding(quote=bad, idempotency_key="k")
    assert result.funded is False
    assert result.external_funding_id is None
    assert result.status == "declined"


@pytest.mark.asyncio
async def test_mock_test_connection_true():
    assert await _mock().test_connection() is True


# ---------------------------------------------------------------------------
# c2fo skeleton — fails closed without a key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_c2fo_quote_fails_closed_without_key():
    adapter = C2FOAdapter({})  # no api_key
    with pytest.raises(RuntimeError, match="api_key"):
        await adapter.quote(
            invoice_amount=Decimal("100.00"),
            currency="USD",
            due_date=_DUE,
            vendor_name="V",
        )


@pytest.mark.asyncio
async def test_c2fo_request_funding_fails_closed_without_key():
    adapter = C2FOAdapter({})
    quote = FinancingQuote(
        provider="c2fo",
        eligible=True,
        discount_percent=Decimal("1.00"),
        fee_percent=Decimal("1.00"),
        funding_date=date.fromisoformat(_FUNDING),
        repayment_date=_DUE,
        advance_amount=Decimal("99.00"),
    )
    with pytest.raises(RuntimeError, match="api_key"):
        await adapter.request_funding(quote=quote, idempotency_key="k")


@pytest.mark.asyncio
async def test_c2fo_test_connection_false_without_key():
    assert await C2FOAdapter({}).test_connection() is False


@pytest.mark.asyncio
async def test_c2fo_with_key_returns_an_ineligible_quote_not_a_crash():
    """The Protocol's own wording: implementations "return an ineligible
    ``FinancingQuote`` rather than raising when the provider simply declines".

    The skeleton used to `raise NotImplementedError` here, so the first caller
    wired to this family would take a 500 from the one path whose contract is
    that it answers "not eligible". Money fields are zeroed and no funding
    date is claimed; the due date the caller supplied is still a fact.
    """
    adapter = C2FOAdapter({"api_key": "live-key"})
    quote = await adapter.quote(
        invoice_amount=Decimal("100.00"),
        currency="USD",
        due_date=_DUE,
        vendor_name="V",
    )
    assert quote.eligible is False
    assert quote.reason == REASON_NOT_IMPLEMENTED
    assert quote.advance_amount == Decimal("0.00")
    assert quote.discount_percent == Decimal("0.00")
    assert quote.fee_percent == Decimal("0.00")
    assert quote.funding_date is None
    assert quote.repayment_date == _DUE
    assert isinstance(quote.advance_amount, Decimal)


@pytest.mark.asyncio
async def test_c2fo_with_key_returns_an_unfunded_result_not_a_crash():
    """Same rule on the money-moving half — and it must not claim the
    financier *declined*: nobody was asked. No money moves, so a repeat call
    with the same idempotency key is trivially idempotent."""
    adapter = C2FOAdapter({"api_key": "live-key"})
    quote = FinancingQuote(
        provider="c2fo",
        eligible=True,
        discount_percent=Decimal("1.00"),
        fee_percent=Decimal("1.00"),
        funding_date=date.fromisoformat(_FUNDING),
        repayment_date=_DUE,
        advance_amount=Decimal("99.00"),
    )
    first = await adapter.request_funding(quote=quote, idempotency_key="k")
    again = await adapter.request_funding(quote=quote, idempotency_key="k")

    assert first.funded is False
    assert first.external_funding_id is None
    assert first.status == "unavailable"
    assert first.reason == REASON_NOT_IMPLEMENTED
    assert first.advance_amount == Decimal("0.00")
    assert first.fee_amount == Decimal("0.00")
    assert again == first


@pytest.mark.asyncio
async def test_c2fo_probe_stays_false_even_fully_credentialed():
    """The refusal is only safe because the probe never claims otherwise —
    an operator learns at configuration time, not on the first quote."""
    assert await C2FOAdapter({"api_key": "live-key", "account_id": "acct"}).test_connection() is (
        False
    )
