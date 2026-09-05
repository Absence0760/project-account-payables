"""The auto-approve threshold — and everything that recommends or enforces it —
is denominated in the org's REPORTING currency.

``auto_approve_below`` is a bare number on a workflow definition's approval step
with no currency of its own. Two surfaces touch it and they used to read it in
two different, unstated ways:

  * ``api/adaptive_workflows._decision_rows`` fed raw ``Invoice.amount`` — in
    whatever currency each invoice was billed in — into
    ``compute_vendor_patterns`` → ``recommend_auto_approve_threshold``, so three
    spotless JPY 100,000 vendors argued for a five-figure threshold on evidence
    worth roughly 650 of the org's own currency; and
  * ``services/extraction.decide_auto_approve`` compared the same raw amount
    against the same bare number, so a ¥1,000,000 invoice read as "below 5,000".

Both now express the amount in the reporting currency at the rate already
LOCKED on the invoice row (never one fetched at gate time), matching the
convention ``payments.cfo_approval_above`` follows via
``payment_controls.cfo_approval_decision``. An amount that cannot be expressed
there fails **closed** on both sides: it is excluded from the evidence that
could raise the threshold, and the floor does not fire, so the invoice goes to
a human.

Every assertion here fails against the previous implementation — the pre-fix
``_decision_rows`` / ``decide_auto_approve`` / ``VendorApprovalPattern`` do not
have the parameters and fields these exercise, and the semantic contrasts
(marked "pre-fix") show what the old numbers were.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.adaptive_workflows import _decision_rows
from app.services.adaptive_workflows import (
    compute_vendor_patterns,
    derive_suggestions,
    recommend_auto_approve_threshold,
)
from app.services.currency_conversion import resolve_reporting_currency
from app.services.extraction import auto_approve_floor_amount, decide_auto_approve

# JPY -> USD, the rate the invoice row locked at approval time.
_JPY_USD = Decimal("0.0065")


# ---------------------------------------------------------------------------
# A stub session that replays the two SELECTs `_decision_rows` issues.
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _StubSession:
    """First `execute` answers the audit_log ⋈ invoices join; the second answers
    `_ready_for_review_starts` (which we leave empty — the timing leg is not
    what this file is about)."""

    def __init__(self, decision_rows):
        self._responses = [decision_rows, []]

    async def execute(self, _q):
        return _Result(self._responses.pop(0) if self._responses else [])


def _row(*, vendor_id, vendor_name, amount, currency, locked_rate, approved_at=None):
    """One `_decision_rows` join row, in the column order the query selects."""
    approved_at = approved_at or datetime.now(UTC)
    reporting_currency = "USD" if locked_rate is not None else None
    reporting_source = currency if locked_rate is not None else None
    return (
        "invoice.approved",  # action
        uuid.uuid4(),  # actor_id
        approved_at,  # created_at
        {},  # details (no corrections -> unmodified)
        uuid.uuid4(),  # invoice id
        vendor_id,  # vendor_id
        vendor_name,  # vendor_name
        Decimal(amount),  # amount
        approved_at - timedelta(days=1),  # inv_created_at
        currency,  # invoice currency
        reporting_currency,  # persisted reporting_currency
        reporting_source,  # persisted reporting_source_currency
        locked_rate,  # persisted reporting_fx_rate
    )


def _jpy_rows(n, *, vendor_id, vendor_name, amount="100000", locked_rate=_JPY_USD):
    return [
        _row(
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            amount=amount,
            currency="JPY",
            locked_rate=locked_rate,
        )
        for _ in range(n)
    ]


# ---------------------------------------------------------------------------
# 1. The recommendation does not move with a foreign vendor's RAW magnitude.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_decision_rows_convert_amounts_into_the_reporting_currency():
    """`_decision_rows` emits reporting-currency amounts, not billed amounts."""
    v = uuid.uuid4()
    db = _StubSession(_jpy_rows(1, vendor_id=v, vendor_name="Kyoto KK"))

    rows = await _decision_rows(
        db, since=datetime.now(UTC) - timedelta(days=365), entity_id=None, reporting_currency="USD"
    )

    assert len(rows) == 1
    # 100,000 JPY at the row's locked 0.0065 = 650.00 USD. Pre-fix this was the
    # raw Decimal("100000") — the number that pushed the recommendation up.
    assert rows[0]["amount"] == Decimal("650.00")
    assert rows[0]["amount_unconverted"] is False


@pytest.mark.asyncio
async def test_recommendation_is_bounded_by_converted_evidence_not_raw_magnitude():
    """Three spotless JPY 100,000 vendors support a ~650 USD threshold, not a
    100,000 one — the bug that could drive `auto_approve_below` to the cap."""
    since = datetime.now(UTC) - timedelta(days=365)
    join_rows: list = []
    for name in ("Kyoto KK", "Osaka KK", "Nagoya KK"):
        join_rows += _jpy_rows(15, vendor_id=uuid.uuid4(), vendor_name=name)

    rows = await _decision_rows(
        _StubSession(join_rows), since=since, entity_id=None, reporting_currency="USD"
    )
    rec = recommend_auto_approve_threshold(
        compute_vendor_patterns(rows), current_threshold=Decimal("0"), currency="USD"
    )

    # Rounded UP to the next 500 from 650 USD.
    assert rec.recommended_threshold == Decimal("1000.00")
    assert rec.currency == "USD"

    # Pre-fix contrast: the same history read at face value recommends a
    # threshold 100x higher, clamped only by the absolute cap.
    raw_rows = [dict(r, amount=Decimal("100000")) for r in rows]
    raw_rec = recommend_auto_approve_threshold(
        compute_vendor_patterns(raw_rows), current_threshold=Decimal("0")
    )
    assert raw_rec.recommended_threshold == Decimal("25000.00")


@pytest.mark.asyncio
async def test_recommendation_rationale_names_its_denomination():
    """The recommendation states what currency its figures are in — the setting
    it targets carries no denomination of its own."""
    join_rows: list = []
    for name in ("A KK", "B KK", "C KK"):
        join_rows += _jpy_rows(15, vendor_id=uuid.uuid4(), vendor_name=name)
    rows = await _decision_rows(
        _StubSession(join_rows),
        since=datetime.now(UTC) - timedelta(days=365),
        entity_id=None,
        reporting_currency="EUR",
    )
    rec = recommend_auto_approve_threshold(
        compute_vendor_patterns(rows), current_threshold=Decimal("0"), currency="EUR"
    )
    assert "EUR" in rec.rationale
    assert "$" not in rec.rationale


# ---------------------------------------------------------------------------
# 2. An amount that can't be converted fails CLOSED on both sides.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unconvertible_rows_are_flagged_and_excluded_from_the_evidence():
    """A foreign invoice with no locked rate can't price a threshold raise."""
    vendors = [uuid.uuid4() for _ in range(3)]
    join_rows: list = []
    for v, name in zip(vendors, ("A KK", "B KK", "C KK"), strict=True):
        join_rows += _jpy_rows(15, vendor_id=v, vendor_name=name, locked_rate=None)

    rows = await _decision_rows(
        _StubSession(join_rows),
        since=datetime.now(UTC) - timedelta(days=365),
        entity_id=None,
        reporting_currency="USD",
    )
    assert all(r["amount_unconverted"] for r in rows)

    patterns = compute_vendor_patterns(rows)
    assert [p.unconverted_count for p in patterns] == [15, 15, 15]
    # The amount aggregates carry no foreign figure at all — not one converted
    # at a guessed rate, and not one at face value.
    assert all(p.max_approved_amount == Decimal("0.00") for p in patterns)

    rec = recommend_auto_approve_threshold(
        patterns, current_threshold=Decimal("0"), currency="USD"
    )
    assert rec.should_raise is False
    assert rec.reason_code == "insufficient_evidence"
    assert rec.qualifying_vendor_count == 0

    # The per-vendor advisory suggestion is gated identically.
    assert derive_suggestions(patterns, currency="USD") == []


def test_gate_fails_closed_when_the_amount_is_not_expressible():
    """`decide_auto_approve`'s amount floor does not fire on an invoice that
    can't be expressed in the currency the floor is denominated in."""
    approval_cfg = {"auto_approve_below": "5000"}

    # Same numbers, convertible: the floor fires.
    assert (
        decide_auto_approve(
            {},
            approval_cfg,
            overall_confidence=0.0,
            amount=Decimal("1000000"),
            reporting_amount=Decimal("650.00"),
            reporting_unconverted=False,
        )
        is True
    )

    # Not expressible: fail closed -> human review.
    assert (
        decide_auto_approve(
            {},
            approval_cfg,
            overall_confidence=0.0,
            amount=Decimal("1000000"),
            reporting_amount=Decimal("1000000"),
            reporting_unconverted=True,
        )
        is False
    )


def test_gate_compares_the_reporting_amount_not_the_billed_amount():
    """A ¥1,000,000 invoice is NOT 'below 5,000' — pre-fix it read as exactly
    that, because the bare threshold was compared against the billed figure."""
    approval_cfg = {"auto_approve_below": "5000"}

    # ¥1,000,000 = USD 6,500 at the locked rate: ABOVE the floor.
    assert (
        decide_auto_approve(
            {},
            approval_cfg,
            overall_confidence=0.0,
            amount=Decimal("1000000"),
            reporting_amount=Decimal("6500.00"),
            reporting_unconverted=False,
        )
        is False
    )
    # Pre-fix reading (billed amount vs. the bare number) — kept to show the
    # comparison this replaced was between two different currencies.
    assert Decimal("1000000") > Decimal("5000")


# ---------------------------------------------------------------------------
# 3. The gate and the recommendation resolve the SAME denomination.
# ---------------------------------------------------------------------------


def test_floor_amount_and_recommendation_share_one_reporting_currency():
    org_settings = {"reporting_currency": "eur"}
    reporting_currency = resolve_reporting_currency(org_settings)
    assert reporting_currency == "EUR"

    invoice = SimpleNamespace(
        amount=Decimal("100000"),
        currency="JPY",
        reporting_currency="EUR",
        reporting_source_currency="JPY",
        reporting_fx_rate=Decimal("0.0060"),
    )
    floor_amount, unconverted = auto_approve_floor_amount(invoice, org_settings)
    assert (floor_amount, unconverted) == (Decimal("600.00"), False)

    # The recommendation the admin applies is denominated in the same currency
    # the gate just compared in — so applying it can't silently change units.
    rec = recommend_auto_approve_threshold(
        [], current_threshold=Decimal("0"), currency=reporting_currency
    )
    assert rec.currency == reporting_currency


def test_floor_amount_fails_closed_when_the_row_lock_does_not_match():
    """A rate locked for a DIFFERENT currency pair (the invoice's currency was
    corrected after the lock) is not trusted — same posture as
    `reporting_amount_at_locked_rate` takes for the payment CFO gate."""
    invoice = SimpleNamespace(
        amount=Decimal("100000"),
        currency="JPY",
        reporting_currency="USD",
        reporting_source_currency="GBP",  # stale: locked for GBP, not JPY
        reporting_fx_rate=Decimal("1.27"),
    )
    _amount, unconverted = auto_approve_floor_amount(invoice, {"reporting_currency": "USD"})
    assert unconverted is True


def test_same_currency_invoice_needs_no_lock():
    """A single-currency tenant is unaffected: rate 1, never `unconverted`."""
    invoice = SimpleNamespace(
        amount=Decimal("1234.50"),
        currency="USD",
        reporting_currency=None,
        reporting_source_currency=None,
        reporting_fx_rate=None,
    )
    assert auto_approve_floor_amount(invoice, {"reporting_currency": "USD"}) == (
        Decimal("1234.50"),
        False,
    )
