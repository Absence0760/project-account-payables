"""The card-rebate rate resolver (api/cards._resolve_rebate_rate).

Rebates used to be hardcoded at 1% for every org, ignoring the documented
`settings.cards.rebate_rate`. The resolver now reads the org's negotiated
rate, defaulting to 1% and parsing defensively so a malformed value can never
break payment settlement.
"""

from __future__ import annotations

from decimal import Decimal

from app.api.cards import _DEFAULT_REBATE_RATE, _resolve_rebate_rate


def test_default_when_no_rate_configured():
    assert _resolve_rebate_rate({}) == _DEFAULT_REBATE_RATE
    assert _resolve_rebate_rate({"enabled": True}) == _DEFAULT_REBATE_RATE


def test_reads_negotiated_rate():
    # The documented float-in-settings form (e.g. 1.5%).
    assert _resolve_rebate_rate({"rebate_rate": 0.015}) == Decimal("0.015")
    # And a string form.
    assert _resolve_rebate_rate({"rebate_rate": "0.0125"}) == Decimal("0.0125")


def test_malformed_rate_falls_back_to_default():
    assert _resolve_rebate_rate({"rebate_rate": "abc"}) == _DEFAULT_REBATE_RATE
    assert _resolve_rebate_rate({"rebate_rate": None}) == _DEFAULT_REBATE_RATE


def test_out_of_range_rate_falls_back_to_default():
    # Negative or implausibly large (>10%) rates are rejected — a fat-finger in
    # settings must not silently pay out a 150% rebate.
    assert _resolve_rebate_rate({"rebate_rate": -0.01}) == _DEFAULT_REBATE_RATE
    assert _resolve_rebate_rate({"rebate_rate": 1.5}) == _DEFAULT_REBATE_RATE


def test_rebate_amount_quantizes_to_cents():
    # 0.015 of 1000.005 → 15.000075, must round to a 2dp money value.
    rate = _resolve_rebate_rate({"rebate_rate": 0.015})
    from decimal import ROUND_HALF_UP

    amount = (Decimal("1000.005") * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    assert amount == Decimal("15.00")
