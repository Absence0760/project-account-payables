"""The adaptive anomaly baseline is built in ONE currency, and so is its subject.

`api/adaptive_workflows._vendor_approved_rows` fed raw `Invoice.amount` — across
every currency the vendor had ever billed in — to `compute_vendor_baseline`,
which takes a mean and a population stdev over them. A mean over mixed
currencies describes nothing: a handful of JPY invoices either make an ordinary
USD one read as a wild outlier, or inflate the stdev enough to hide a genuine
one. This is the same defect already fixed for the `stat_anomaly` fraud rule
(`test_stat_anomaly_currency_realdb.py`).

Converting only the baseline would have created a NEW mismatch, because
`detect_invoice_anomaly` read the subject's amount straight off the Invoice
object — a converted baseline against a billed subject. So the function's input
contract changed instead: `amount` is a REQUIRED keyword, the subject expressed
in the baseline's currency, and `None` means it could not be. There is no
default to forget.

The surface is advisory and writes no warning and no Exception row, so an
unpriceable subject **abstains** on the two amount rules and says so with an
`amount_comparison_unavailable` info flag, rather than fabricating a verdict.
The approver and timing rules still run — neither depends on the currency.

Every test fails against the previous implementation: `detect_invoice_anomaly`
took no `amount`, `compute_vendor_baseline` took no `currency` and had no
`unconverted_count`, and `_vendor_approved_rows` took no `reporting_currency`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.api.adaptive_workflows import _subject_amount, _vendor_approved_rows
from app.services.adaptive_workflows import compute_vendor_baseline, detect_invoice_anomaly

_JPY_USD = Decimal("0.0065")


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _StubSession:
    """First `execute` answers the invoice ⋈ audit_log join, the second the
    `ready_for_review` clock-starts (left empty — timing isn't what this is
    about)."""

    def __init__(self, rows):
        self._responses = [rows, []]

    async def execute(self, _q):
        return _Result(self._responses.pop(0) if self._responses else [])


_NOW = datetime.now(UTC)


def _row(amount, currency="USD", *, rate=None, source=None, approver="A"):
    """One `_vendor_approved_rows` join row, in the query's column order."""
    return (
        uuid.uuid4(),  # invoice id
        Decimal(str(amount)),  # amount
        approver,  # audit actor_id
        _NOW,  # audit created_at
        _NOW - timedelta(days=1),  # invoice created_at
        currency,  # invoice currency
        "USD" if rate is not None else None,  # reporting_currency
        source if rate is not None else None,  # reporting_source_currency
        None if rate is None else Decimal(str(rate)),  # reporting_fx_rate
    )


def _invoice(amount, currency="USD", *, rate=None, source=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        vendor_id="V1",
        vendor_name="Acme",
        amount=Decimal(str(amount)),
        currency=currency,
        reporting_currency="USD" if rate is not None else None,
        reporting_source_currency=source if rate is not None else None,
        reporting_fx_rate=None if rate is None else Decimal(str(rate)),
    )


async def _rows_for(join_rows, currency="USD"):
    return await _vendor_approved_rows(
        _StubSession(join_rows),
        vendor_id=uuid.uuid4(),
        vendor_name="Acme",
        entity_id=None,
        reporting_currency=currency,
    )


# ---------------------------------------------------------------------------
# The baseline is denominated, not mixed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_amounts_are_converted_into_the_reporting_currency():
    rows = await _rows_for([_row("100000", "JPY", rate=_JPY_USD, source="JPY")] * 6)
    assert all(r["amount"] == Decimal("650.00") for r in rows)
    assert not any(r["amount_unconverted"] for r in rows)

    baseline = compute_vendor_baseline(rows, vendor_name="Acme", min_history=5, currency="USD")
    assert baseline is not None
    assert baseline.mean_amount == Decimal("650.00")
    assert baseline.currency == "USD"
    assert baseline.unconverted_count == 0


@pytest.mark.asyncio
async def test_a_foreign_invoice_no_longer_makes_a_domestic_one_an_outlier():
    """Five USD 5,000 invoices plus one JPY 1,000,000 (= USD 6,500). Read at
    face value the JPY row drags the mean to ~171,000 with a huge stdev and the
    vendor's ordinary 5,000 invoice becomes uncomparable; converted, the
    baseline is a tight band around 5,250."""
    join_rows = [_row("5000")] * 5 + [_row("1000000", "JPY", rate=_JPY_USD, source="JPY")]
    rows = await _rows_for(join_rows)
    baseline = compute_vendor_baseline(rows, vendor_name="Acme", min_history=5, currency="USD")

    assert baseline.mean_amount == Decimal("5250.00")
    assert baseline.max_amount == Decimal("6500.00")

    # Pre-fix contrast: the same history read at face value.
    raw = [{"amount": Decimal("5000")} for _ in range(5)] + [{"amount": Decimal("1000000")}]
    raw_baseline = compute_vendor_baseline(raw, vendor_name="Acme", min_history=5)
    assert raw_baseline.mean_amount > Decimal("170000")
    assert raw_baseline.stdev_amount > Decimal("370000")


@pytest.mark.asyncio
async def test_unconvertible_history_is_excluded_and_counted():
    """A row with no locked rate contributes no amount at all — not a converted
    guess, and not its face value."""
    join_rows = [_row("5000")] * 5 + [_row("1000000", "JPY")]  # last one never materialised
    rows = await _rows_for(join_rows)
    assert [r["amount_unconverted"] for r in rows] == [False] * 5 + [True]

    baseline = compute_vendor_baseline(rows, vendor_name="Acme", min_history=5, currency="USD")
    assert baseline.sample_size == 5
    assert baseline.unconverted_count == 1
    assert baseline.mean_amount == Decimal("5000.00")
    assert baseline.max_amount == Decimal("5000.00")
    # Its approver still counts — that signal is currency-independent, and
    # dropping it would make a legitimate approver look unusual.
    assert baseline.typical_approver_ids == ["A"]


@pytest.mark.asyncio
async def test_a_baseline_needs_min_history_ROWS_IT_CAN_PRICE():
    """Ten approvals, only two priceable, is not a baseline."""
    join_rows = [_row("5000")] * 2 + [_row("1000000", "JPY")] * 8
    rows = await _rows_for(join_rows)
    assert compute_vendor_baseline(rows, vendor_name="Acme", min_history=5, currency="USD") is None


# ---------------------------------------------------------------------------
# The subject is compared in the baseline's currency, or not at all.
# ---------------------------------------------------------------------------


def _usd_baseline(rows_amount="5000", n=6):
    rows = [
        {"amount": Decimal(rows_amount), "approver_id": "A", "time_to_approve_days": None}
        for _ in range(n)
    ]
    return compute_vendor_baseline(rows, vendor_name="Acme", min_history=5, currency="USD")


def test_subject_is_compared_after_conversion():
    """JPY 5,000,000 = USD 32,500 against a 5,000 baseline — an outlier. Its
    billed figure would have been an outlier too, but for the wrong reason and
    by the wrong multiple; the reverse case below is the one that mattered."""
    baseline = _usd_baseline()
    inv = _invoice("5000000", "JPY", rate=_JPY_USD, source="JPY")
    res = detect_invoice_anomaly(
        inv, baseline, amount=_subject_amount(inv, "USD"), sigma=Decimal("2.0")
    )
    assert res.amount == Decimal("32500.00")
    assert "amount_high" in [f.code for f in res.flags]


def test_an_ordinary_foreign_invoice_is_not_flagged_on_its_billed_magnitude():
    """JPY 800,000 = USD 5,200 against a 5,000 baseline — ordinary. Pre-fix the
    raw 800,000 tripped amount_high on a vendor whose norm it matches."""
    baseline = _usd_baseline()
    inv = _invoice("800000", "JPY", rate=_JPY_USD, source="JPY")
    res = detect_invoice_anomaly(inv, baseline, amount=_subject_amount(inv, "USD"))
    assert [f.code for f in res.flags] == []

    # The pre-fix reading: the billed figure against the same baseline.
    pre_fix = detect_invoice_anomaly(inv, baseline, amount=Decimal("800000"))
    assert "amount_high" in [f.code for f in pre_fix.flags]


def test_an_unpriceable_subject_abstains_and_says_so():
    """Advisory surface: no verdict beats a fabricated one."""
    baseline = _usd_baseline()
    inv = _invoice("800000", "JPY")  # no locked rate
    assert _subject_amount(inv, "USD") is None

    res = detect_invoice_anomaly(inv, baseline, amount=None)
    codes = [f.code for f in res.flags]
    assert codes == ["amount_comparison_unavailable"]
    assert "amount_high" not in codes and "amount_low" not in codes
    assert "USD" in res.flags[0].message
    # The display figure falls back to the billed amount — never compared.
    assert res.amount == Decimal("800000")


def test_an_unpriceable_subject_still_gets_the_currency_free_rules():
    """The approver and timing rules don't depend on a currency, so they run."""
    rows = [
        {"amount": Decimal("5000"), "approver_id": "A", "time_to_approve_days": Decimal("2")}
        for _ in range(6)
    ]
    baseline = compute_vendor_baseline(rows, vendor_name="Acme", min_history=5, currency="USD")
    inv = _invoice("800000", "JPY")

    res = detect_invoice_anomaly(
        inv,
        baseline,
        amount=None,
        proposed_approver_id="Z",
        time_in_review_days=Decimal("30"),
    )
    codes = set(f.code for f in res.flags)
    assert {"amount_comparison_unavailable", "unusual_approver", "off_pattern_timing"} == codes


def test_insufficient_history_is_unchanged():
    inv = _invoice("800000", "JPY")
    res = detect_invoice_anomaly(inv, None, amount=None)
    assert res.insufficient_history is True
    assert res.flags == []
