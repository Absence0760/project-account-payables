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


# ---------------------------------------------------------------------------
# Deeper regression coverage (beyond the landed happy path)
# ---------------------------------------------------------------------------


def test_the_settled_figure_is_used_even_when_it_exceeds_the_authorization():
    """`settled > authorized` is not treated as suspect and is NOT clamped —
    the old expression could only ever report the auth, so an over-settlement
    was silently under-rebated. (`payment_settlement` is where an
    over-settlement is *judged*; this helper only reports what settled.)"""
    base, source = resolve_rebate_base(Decimal("10000.00"), Decimal("1.00"))
    assert base == Decimal("10000.00")
    assert source == "settled"


def test_a_negative_settlement_is_reported_as_settled_not_swapped_for_the_auth():
    """A negative figure is truthy, so the old `or` chain would have kept
    `amount_charged` (the auth) and reported a POSITIVE rebate on a settlement
    that went the other way. The rule is 'what the event said moved'; the sign
    is preserved so a reconciliation can see it rather than being told a
    plausible positive number."""
    base, source = resolve_rebate_base(Decimal("-40.00"), Decimal("100.00"))
    assert base == Decimal("-40.00")
    assert source == "settled"


def test_a_negative_authorization_is_still_second_best_evidence_not_the_limit():
    base, source = resolve_rebate_base(None, Decimal("-40.00"))
    assert base == Decimal("-40.00")
    assert source == "authorized"


def test_scale_survives_so_no_float_hop_happened():
    """A `float` round-trip destroys a Decimal's scale (`Decimal("80.00")` ->
    `80.0`). Pinning the exponent is therefore a float detector, not just a
    value check — the money invariant, asserted structurally."""
    base, _ = resolve_rebate_base(Decimal("80.00"), None)
    assert base.as_tuple().exponent == -2
    assert str(base) == "80.00"
    assert not isinstance(base, float)


def test_a_sub_cent_settlement_is_not_rounded_by_the_helper():
    """Quantization belongs to the caller (which rounds the REBATE, not the
    base). Rounding here would compound two roundings on one figure."""
    base, source = resolve_rebate_base(Decimal("0.0001"), None)
    assert base == Decimal("0.0001")
    assert source == "settled"


def test_a_very_large_settlement_is_exact_to_the_cent():
    """`Numeric(15, 2)` tops out around 10**13; a float would have lost cents
    well before here."""
    base, _ = resolve_rebate_base(Decimal("9999999999999.99"), None)
    assert base == Decimal("9999999999999.99")
    assert base - Decimal("9999999999999.98") == Decimal("0.01")


def test_the_returned_source_is_a_closed_pii_free_vocabulary():
    """The value rides an append-only audit row, so it is a fixed token — not
    a message, and never a provider-supplied string."""
    sources = {
        resolve_rebate_base(Decimal("1"), Decimal("2"))[1],
        resolve_rebate_base(Decimal("0"), None)[1],
        resolve_rebate_base(None, Decimal("2"))[1],
        resolve_rebate_base(None, None)[1],
        resolve_rebate_base(None, Decimal("0"))[1],
    }
    assert sources == {"settled", "authorized", "unknown"}
    assert all(s.isalpha() and s.islower() for s in sources)


def test_the_helper_is_pure_and_takes_no_card():
    """It cannot read `amount_limit` off a card either — a `VirtualCard` is
    not one of its parameters, so there is nothing to reach through."""
    import inspect

    sig = inspect.signature(resolve_rebate_base)
    annotations = {p.annotation for p in sig.parameters.values()}
    assert annotations == {Decimal | None}, annotations
    # Two calls with the same inputs give the same answer, and nothing is
    # mutated (there is no argument that could be).
    assert resolve_rebate_base(Decimal("7.77"), Decimal("9.99")) == resolve_rebate_base(
        Decimal("7.77"), Decimal("9.99")
    )


def test_no_call_site_passes_the_limit_to_the_rebate_base():
    """The structural guard on the *call sites*, not just the signature: a
    future refactor that reintroduces `card.amount_limit` as the fallback —
    the original defect — fails here even if it keeps the helper's shape.

    Read off the AST of the real module, so it cannot be satisfied by a
    comment."""
    import ast
    import pathlib

    src = pathlib.Path("app/api/cards.py").read_text()
    tree = ast.parse(src)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_rebate_base"
    ]
    assert calls, "the settlement branch no longer calls resolve_rebate_base"
    for call in calls:
        assert not call.keywords, "the two figures are passed positionally at the money path"
        assert len(call.args) == 2
        rendered = [ast.unparse(a) for a in call.args]
        assert not any("limit" in r for r in rendered), rendered
        # And the arguments are exactly the settled figure and the auth one.
        assert rendered == ["settled_amount", "card.amount_charged"], rendered


def test_the_provider_amount_becomes_a_decimal_before_it_reaches_the_helper():
    """Providers send the amount as a JSON string or number, never a Decimal.
    `_normalize_charge_amount` is the boundary that makes it exact — feeding a
    raw `str` through would make `base * rate` a TypeError inside the money
    path, so the two are pinned together here."""
    from app.api.cards import _normalize_charge_amount

    for raw in ("80.00", 80, 80.0):
        settled = _normalize_charge_amount("nium", raw, None, "USD")
        assert isinstance(settled, Decimal)
        base, source = resolve_rebate_base(settled, Decimal("100.00"))
        assert source == "settled"
        assert base == Decimal("80")
        # The rebate arithmetic the call site performs must not raise.
        assert (base * _resolve_rebate_rate({})).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        ) == Decimal("0.80")


def test_an_absent_or_unparseable_provider_amount_falls_back_to_the_charged_figure():
    """The fallback chain end to end, at the shape the call site builds: the
    settlement normalizer is given `fallback=None` precisely so a missing
    amount reaches `resolve_rebate_base` as `None` and lands on the AUTHORIZED
    figure — never on the card's limit, which is what the old
    `card.amount_charged or card.amount_limit` did."""
    from app.api.cards import _normalize_charge_amount

    charged = Decimal("250.00")
    for raw in (None, 0, "", "not-a-number"):
        settled = _normalize_charge_amount("nium", raw, None, "USD")
        assert settled is None, raw
        base, source = resolve_rebate_base(settled, charged)
        assert (base, source) == (charged, "authorized"), raw


def test_the_rebate_is_exact_at_the_half_cent_boundary():
    """`ROUND_HALF_UP` on an exact Decimal product. 14.50 x 1% is exactly
    0.145, which rounds to 0.15; the same product computed in binary floats is
    0.14499999999999999 under some orderings and would round to 0.14."""
    base, _ = resolve_rebate_base(Decimal("14.50"), None)
    rate = Decimal("0.0100")
    assert base * rate == Decimal("0.145000")
    assert (base * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == Decimal("0.15")
