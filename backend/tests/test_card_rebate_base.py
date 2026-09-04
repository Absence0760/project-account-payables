"""The rebate is earned on the SETTLED amount, never on the card's limit.

Two defects in one line. `api/cards` computed the rebate base as
`card.amount_charged or card.amount_limit`:

  * `amount_charged` is stamped by the AUTHORIZATION event and never updated at
    settlement, so the rebate was computed on the authorized figure while the
    settlement event's own `amount` sat unused in scope. A card network's
    settlement routinely differs from the auth it clears (partial capture,
    tips, fuel adjustments) and the processor pays rebate on what settled, so
    the figure was systematically wrong wherever the two diverge.
  * The `or` fallback reached for `amount_limit` — the card's authorization
    CEILING, not spend. A settlement arriving without a usable amount, on a card
    whose auth was also missing, rebated on the full limit: a $10,000 card that
    settled $100 earned a rebate on $10,000.

Recorded as an unverified lead in `docs/followups.md` ("Card rebate base may be
the authorized rather than the settled amount"); confirmed here.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pytest

from app.api.cards import _resolve_rebate_rate, resolve_rebate_base


def test_settled_amount_wins_over_the_authorization():
    """The whole point: a settlement that differs from the auth is what counts."""
    base, source = resolve_rebate_base(Decimal("80.00"), Decimal("100.00"))
    assert base == Decimal("80.00")
    assert source == "settled"


def test_a_settlement_larger_than_the_auth_is_also_honoured():
    """Over-settlement (a tip, a fuel adjustment) is still the settled figure —
    the rule is 'what moved', not 'the smaller of the two'."""
    base, source = resolve_rebate_base(Decimal("115.00"), Decimal("100.00"))
    assert base == Decimal("115.00")
    assert source == "settled"


def test_zero_settlement_is_a_real_figure_not_a_missing_one():
    """`Decimal("0")` is falsy — the old `or` chain would have skipped straight
    past a genuine zero settlement to the auth, and then to the limit."""
    base, source = resolve_rebate_base(Decimal("0"), Decimal("100.00"))
    assert base == Decimal("0")
    assert source == "settled"


def test_falls_back_to_the_authorization_when_the_settlement_carries_no_amount():
    """Some rails send a bare settlement envelope. The auth is then the best
    evidence available, and the source records that it was second-best."""
    base, source = resolve_rebate_base(None, Decimal("100.00"))
    assert base == Decimal("100.00")
    assert source == "authorized"


def test_no_evidence_at_all_rebates_on_zero_not_the_limit():
    """The defect: with neither figure, the old code reached for the card's
    limit. The honest base is zero."""
    base, source = resolve_rebate_base(None, None)
    assert base == Decimal("0")
    assert source == "unknown"


@pytest.mark.parametrize("empty", [None, Decimal("0")])
def test_a_missing_authorization_never_becomes_a_limit(empty):
    base, source = resolve_rebate_base(None, empty)
    assert base == Decimal("0")
    assert source == "unknown"


def test_the_limit_is_not_an_input_at_all():
    """Structural: the helper cannot reach the limit even by accident, because
    it is not passed one. This is what stops the bug returning."""
    import inspect

    params = set(inspect.signature(resolve_rebate_base).parameters)
    assert params == {"settled_amount", "authorized_amount"}
    assert not any("limit" in p for p in params)


def test_the_source_distinguishes_every_case():
    """The audit row carries this so a reconciliation against the processor's
    statement can tell a settled base from an authorized one."""
    assert resolve_rebate_base(Decimal("1"), Decimal("2"))[1] == "settled"
    assert resolve_rebate_base(None, Decimal("2"))[1] == "authorized"
    assert resolve_rebate_base(None, None)[1] == "unknown"


def test_base_stays_exact_decimal():
    """Money is exact — the helper must not round, scale, or float-ify."""
    base, _ = resolve_rebate_base(Decimal("1234.567"), None)
    assert base == Decimal("1234.567")
    assert isinstance(base, Decimal)


def test_the_rebate_computed_from_a_settled_base_differs_from_the_authorized_one():
    """End-to-end arithmetic on the two figures, at the default rate: the whole
    reason the base matters."""
    rate = _resolve_rebate_rate({})

    settled_base, _ = resolve_rebate_base(Decimal("80.00"), Decimal("100.00"))
    settled_rebate = (settled_base * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    authorized_rebate = (Decimal("100.00") * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    assert settled_rebate < authorized_rebate
    assert settled_rebate == (Decimal("80.00") * rate).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def test_a_large_limit_can_no_longer_inflate_a_small_settlement():
    """The headline case from the lead: a $10,000 card that settles $100."""
    rate = _resolve_rebate_rate({})
    base, source = resolve_rebate_base(Decimal("100.00"), None)
    assert source == "settled"
    rebate = (base * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # A rebate on the $10,000 limit at the 1% default would have been $100.00.
    assert rebate == Decimal("1.00")
