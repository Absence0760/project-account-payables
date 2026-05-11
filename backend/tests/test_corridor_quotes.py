"""Multi-route corridor optimization — services/corridor_quotes.py.

Pins:
  - `compare_quotes` aggregates quotes from N configured processors
    and ranks them (cheapest by default; fastest on request)
  - A flaky / raising adapter is treated as unavailable, not as a
    poisoned ranking; the next provider wins
  - An adapter that doesn't support the requested method returns
    unavailable; doesn't pollute the ranking
  - When every provider says "no", `NoEligibleCorridorError` raises
    (NOT a silent fallback) so the caller can fail the payment
    with a specific reason
  - `savings_vs_runner_up` reports the cheapest-vs-second-cheapest
    delta for UI display
  - Tie-breakers: cost first, then ETA, then provider name (stable)
  - Org settings new shape `payments.providers=[...]` overrides the
    legacy `payments.provider`; missing both raises
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from app.services.corridor_quotes import (
    NoEligibleCorridorError,
    compare_quotes,
    savings_vs_runner_up,
)
from app.services.payment_adapters import CorridorQuote, PaymentPayload


def _payload(*, amount=Decimal("1000.00"), method="ach", currency="USD"):
    return PaymentPayload(
        correlation_id="cor-1",
        invoice_id="inv-1",
        invoice_number="INV-1",
        vendor_name="Test",
        amount=amount,
        currency=currency,
        method=method,
        target_country="US",
    )


class _FakeAdapter:
    """Used in place of a real PaymentAdapter to return canned
    quotes. Implements just the surface compare_quotes touches."""

    def __init__(
        self,
        *,
        provider_name: str,
        quote: CorridorQuote | None = None,
        raises: Exception | None = None,
    ):
        self.provider_name = provider_name
        self._quote = quote
        self._raises = raises

    async def quote_payment(self, payload):
        if self._raises is not None:
            raise self._raises
        return self._quote


def _settings_with_providers(*names: str) -> dict:
    return {"payments": {"providers": [{"provider": n} for n in names]}}


# ---------------------------------------------------------------------------
# Cheapest mode — basic ranking.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cheapest_provider_wins_when_pct_fees_differ():
    """Two providers, same method, different fees → cheaper wins."""
    cheap = _FakeAdapter(
        provider_name="cheap",
        quote=CorridorQuote(
            provider="cheap",
            method="ach",
            available=True,
            pct_fee=Decimal("0.001"),
            eta_business_days=2,
        ),
    )
    pricey = _FakeAdapter(
        provider_name="pricey",
        quote=CorridorQuote(
            provider="pricey",
            method="ach",
            available=True,
            pct_fee=Decimal("0.005"),
            eta_business_days=1,
        ),
    )
    settings = _settings_with_providers("cheap", "pricey")
    with patch(
        "app.services.corridor_quotes.get_payment_adapter",
        side_effect=[cheap, pricey],
    ):
        ranking = await compare_quotes(_payload(), settings)
    assert ranking.winner.provider == "cheap"
    assert ranking.mode == "cheapest"
    assert len(ranking.runners_up) == 1
    assert ranking.runners_up[0].provider == "pricey"


@pytest.mark.asyncio
async def test_savings_vs_runner_up_computes_delta_at_payment_amount():
    """Winner $1.00 cheaper per $1000 → savings_vs_runner_up returns
    that exact amount (the executor uses it for the UI badge)."""
    cheap = _FakeAdapter(
        provider_name="cheap",
        quote=CorridorQuote(
            provider="cheap",
            method="ach",
            available=True,
            pct_fee=Decimal("0.001"),  # 1.00 on 1000
        ),
    )
    pricey = _FakeAdapter(
        provider_name="pricey",
        quote=CorridorQuote(
            provider="pricey",
            method="ach",
            available=True,
            pct_fee=Decimal("0.005"),  # 5.00 on 1000
        ),
    )
    settings = _settings_with_providers("cheap", "pricey")
    with patch(
        "app.services.corridor_quotes.get_payment_adapter",
        side_effect=[cheap, pricey],
    ):
        ranking = await compare_quotes(_payload(amount=Decimal("1000.00")), settings)
    assert savings_vs_runner_up(ranking, Decimal("1000.00")) == Decimal("4.000")


@pytest.mark.asyncio
async def test_unavailable_provider_does_not_win_even_if_zero_fee():
    """An adapter that says `available=False` has total_cost = +∞,
    so it can never win. Pin against a regression that filtered to
    `available=True` AFTER ranking — that'd let an unavailable
    quote with `fee=0` rank first."""
    bad = _FakeAdapter(
        provider_name="bad",
        quote=CorridorQuote(
            provider="bad",
            method="ach",
            available=False,
            pct_fee=Decimal("0"),  # zero fee but unavailable
            unavailable_reason="capacity_reached",
        ),
    )
    good = _FakeAdapter(
        provider_name="good",
        quote=CorridorQuote(
            provider="good",
            method="ach",
            available=True,
            pct_fee=Decimal("0.005"),
        ),
    )
    settings = _settings_with_providers("bad", "good")
    with patch(
        "app.services.corridor_quotes.get_payment_adapter",
        side_effect=[bad, good],
    ):
        ranking = await compare_quotes(_payload(), settings)
    assert ranking.winner.provider == "good"


@pytest.mark.asyncio
async def test_adapter_exception_does_not_propagate_and_treated_as_unavailable():
    """If a provider's quote endpoint raises, we log the class name
    and treat it as unavailable. Crucially the raw exception
    message is NOT in the ranking output (it might carry PANs)."""
    flaky = _FakeAdapter(
        provider_name="flaky",
        raises=RuntimeError("internal error: account=4111-1111-1111-1234"),
    )
    good = _FakeAdapter(
        provider_name="good",
        quote=CorridorQuote(
            provider="good",
            method="ach",
            available=True,
            pct_fee=Decimal("0.005"),
        ),
    )
    settings = _settings_with_providers("flaky", "good")
    with patch(
        "app.services.corridor_quotes.get_payment_adapter",
        side_effect=[flaky, good],
    ):
        ranking = await compare_quotes(_payload(), settings)
    assert ranking.winner.provider == "good"
    flaky_runner = next(q for q in ranking.runners_up if q.provider == "flaky")
    # PII guardrail: the unavailable_reason must NOT contain the
    # raw account-number-shaped error message.
    assert "4111" not in (flaky_runner.unavailable_reason or "")
    assert "RuntimeError" in (flaky_runner.unavailable_reason or "")


# ---------------------------------------------------------------------------
# Fastest mode.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fastest_mode_prefers_lower_eta_even_if_pricier():
    """Org sets `mode="fastest"`. Slower-but-cheaper loses to
    faster-but-pricier."""
    slow = _FakeAdapter(
        provider_name="slow",
        quote=CorridorQuote(
            provider="slow",
            method="ach",
            available=True,
            pct_fee=Decimal("0.001"),
            eta_business_days=3,
        ),
    )
    fast = _FakeAdapter(
        provider_name="fast",
        quote=CorridorQuote(
            provider="fast",
            method="ach",
            available=True,
            pct_fee=Decimal("0.005"),
            eta_business_days=0,
        ),
    )
    settings = _settings_with_providers("slow", "fast")
    with patch(
        "app.services.corridor_quotes.get_payment_adapter",
        side_effect=[slow, fast],
    ):
        ranking = await compare_quotes(_payload(), settings, mode="fastest")
    assert ranking.winner.provider == "fast"
    assert ranking.mode == "fastest"


# ---------------------------------------------------------------------------
# All-fail path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_eligible_corridor_raises_when_every_provider_unavailable():
    """Every adapter says no → NoEligibleCorridorError, caller fails
    the payment with a specific reason. No silent fallback."""
    a = _FakeAdapter(
        provider_name="a",
        quote=CorridorQuote(
            provider="a",
            method="sepa",
            available=False,
            unavailable_reason="method 'sepa' not supported",
        ),
    )
    b = _FakeAdapter(
        provider_name="b",
        quote=CorridorQuote(
            provider="b",
            method="sepa",
            available=False,
            unavailable_reason="method 'sepa' not supported",
        ),
    )
    settings = _settings_with_providers("a", "b")
    with patch(
        "app.services.corridor_quotes.get_payment_adapter",
        side_effect=[a, b],
    ):
        with pytest.raises(NoEligibleCorridorError, match="no provider can quote"):
            await compare_quotes(_payload(method="sepa"), settings)


@pytest.mark.asyncio
async def test_no_providers_configured_raises_immediately():
    """Org has neither `payments.providers` nor `payments.provider`
    set. compare_quotes raises without calling any adapter."""
    with pytest.raises(NoEligibleCorridorError, match="no payment providers"):
        await compare_quotes(_payload(), {"payments": {}})


# ---------------------------------------------------------------------------
# Legacy single-provider shape compatibility.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_single_provider_shape_is_accepted():
    """Pre-multi-route orgs have `payments.provider = "mock"`
    without a `providers` list. compare_quotes lifts it into a
    single-element list so the optimizer is a no-op (winner = that
    sole provider's quote)."""
    only = _FakeAdapter(
        provider_name="mock",
        quote=CorridorQuote(
            provider="mock",
            method="ach",
            available=True,
            pct_fee=Decimal("0.001"),
        ),
    )
    settings = {"payments": {"provider": "mock"}}
    with patch("app.services.corridor_quotes.get_payment_adapter", return_value=only):
        ranking = await compare_quotes(_payload(), settings)
    assert ranking.winner.provider == "mock"
    assert ranking.runners_up == []
    # No savings to report when there's nothing to compare against.
    assert savings_vs_runner_up(ranking, Decimal("1000.00")) == Decimal("0")


# ---------------------------------------------------------------------------
# De-duplication when the same provider is configured twice.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_provider_in_settings_is_deduped_to_first_occurrence():
    """If an org accidentally lists the same provider twice in
    `payments.providers`, the optimizer must NOT rank it against
    itself (which would always tie at zero savings). The dedupe
    keeps the first occurrence."""
    only = _FakeAdapter(
        provider_name="dup",
        quote=CorridorQuote(
            provider="dup",
            method="ach",
            available=True,
            pct_fee=Decimal("0.002"),
        ),
    )
    only2 = _FakeAdapter(
        provider_name="dup",
        quote=CorridorQuote(
            provider="dup",
            method="ach",
            available=True,
            pct_fee=Decimal("0.002"),
        ),
    )
    settings = {"payments": {"providers": [{"provider": "dup"}, {"provider": "dup"}]}}
    with patch(
        "app.services.corridor_quotes.get_payment_adapter",
        side_effect=[only, only2],
    ):
        ranking = await compare_quotes(_payload(), settings)
    # Only one quote made it through.
    assert ranking.winner.provider == "dup"
    assert ranking.runners_up == []
