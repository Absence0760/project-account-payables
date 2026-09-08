"""The two `/api/adaptive` read models must not mislabel what they report.

Both defects these lock down are wrong *numbers on screen*, not missing ones:

1. ``InvoiceAnomalyResponse.amount`` is not always in the reporting currency.
   When the subject invoice carries no usable rate lock, ``detect_invoice_anomaly``
   falls back to the BILLED figure "for DISPLAY only" — and the response emitted
   no currency at all, so every client labelled it with the org's reporting
   currency. ``amount_currency`` is now required at the serialiser so there is
   no default to forget, the same reason ``amount`` itself is a required
   argument on ``detect_invoice_anomaly``.

2. ``VendorPatternResponse``'s four money fields EXCLUDE approvals that could
   not be expressed in the reporting currency, while ``sample_size`` still
   counts them. The count was withheld, so an average over N-minus-k rows was
   presented beside a sample count of N with nothing to say so
   (decisions.md §79/§82).

Pure unit tests — no DB. Both serialisers are plain functions for exactly that
reason.
"""

from decimal import Decimal
from types import SimpleNamespace

from app.api.adaptive_workflows import (
    _anomaly_amount_currency,
    _anomaly_dict,
    _vendor_pattern_dict,
)
from app.schemas.adaptive_workflows import InvoiceAnomalyResponse, VendorPatternResponse
from app.services.adaptive_workflows import (
    AnomalyFlag,
    InvoiceAnomaly,
    VendorApprovalPattern,
    compute_vendor_baseline,
    detect_invoice_anomaly,
)


def _invoice(**over):
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        vendor_id=None,
        vendor_name="Steady Vendor",
        amount=Decimal("48000.00"),
        currency="JPY",
    )
    base.update(over)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# 1. amount_currency
# ---------------------------------------------------------------------------


def test_convertible_amount_is_labelled_with_the_reporting_currency():
    inv = _invoice(currency="JPY")
    assert _anomaly_amount_currency(inv, Decimal("310.00"), reporting_currency="usd") == "USD"


def test_unconvertible_amount_is_labelled_with_the_billed_currency():
    """`None` subject == the displayed figure is the BILLED one, so the honest
    label is the invoice's own currency — never the one it could not reach."""
    inv = _invoice(currency="jpy")
    assert _anomaly_amount_currency(inv, None, reporting_currency="USD") == "JPY"


def test_a_blank_currency_stays_blank_rather_than_guessing():
    inv = _invoice(currency="")
    assert _anomaly_amount_currency(inv, None, reporting_currency="USD") == ""
    assert _anomaly_amount_currency(inv, Decimal("1"), reporting_currency="") == ""


def test_anomaly_dict_carries_the_currency_through_the_response_model():
    anomaly = InvoiceAnomaly(
        invoice_id="11111111-1111-1111-1111-111111111111",
        vendor_id=None,
        vendor_name="Steady Vendor",
        amount=Decimal("48000.00"),
        baseline=None,
        flags=[
            AnomalyFlag(
                code="amount_comparison_unavailable",
                severity="info",
                message="…",
                observed="48000.00",
                expected="310.00",
            )
        ],
        insufficient_history=False,
    )
    payload = _anomaly_dict(anomaly, amount_currency="JPY")
    assert payload["amount"] == "48000.00"
    assert payload["amount_currency"] == "JPY"
    assert InvoiceAnomalyResponse(**payload).amount_currency == "JPY"


def test_the_unconvertible_path_end_to_end_reports_the_billed_currency():
    """The whole reason the field exists: an invoice with no rate lock is
    flagged `amount_comparison_unavailable`, keeps its billed figure, and must
    be labelled in the currency that figure is actually in."""
    history = [
        {"amount": Decimal("310.00"), "approver_id": "a", "time_to_approve_days": Decimal("1")}
        for _ in range(6)
    ]
    baseline = compute_vendor_baseline(history, vendor_name="Steady Vendor", currency="USD")
    assert baseline is not None

    inv = _invoice(currency="JPY", amount=Decimal("48000.00"))
    anomaly = detect_invoice_anomaly(inv, baseline, amount=None)

    codes = [f.code for f in anomaly.flags]
    assert "amount_comparison_unavailable" in codes
    assert "amount_high" not in codes  # abstained, as designed
    assert anomaly.amount == Decimal("48000.00")  # the BILLED figure

    payload = _anomaly_dict(
        anomaly,
        amount_currency=_anomaly_amount_currency(inv, None, reporting_currency="USD"),
    )
    assert payload["amount_currency"] == "JPY"


# ---------------------------------------------------------------------------
# 2. unconverted_count on the vendor patterns
# ---------------------------------------------------------------------------


def _pattern(**over):
    base = dict(
        vendor_id=None,
        vendor_name="Steady Vendor",
        approved_count=10,
        rejected_count=2,
        approval_rate_pct=Decimal("83.3"),
        unmodified_count=9,
        consistency_pct=Decimal("90.0"),
        avg_approved_amount=Decimal("310.00"),
        median_approved_amount=Decimal("300.00"),
        min_approved_amount=Decimal("100.00"),
        max_approved_amount=Decimal("900.00"),
        sample_size=12,
        unconverted_count=3,
    )
    base.update(over)
    return VendorApprovalPattern(**base)


def test_vendor_pattern_dict_discloses_the_excluded_approvals():
    payload = _vendor_pattern_dict(_pattern())
    # The average is over 10 - 3 = 7 rows while the sample says 12: the count
    # is what lets a reader know that, and it must survive to the wire.
    assert payload["unconverted_count"] == 3
    assert payload["sample_size"] == 12
    assert VendorPatternResponse(**payload).unconverted_count == 3


def test_vendor_pattern_dict_covers_every_response_field():
    """Drift guard: the serialiser is a hand-written literal, so a field added
    to either side has to be added to the other. Without this, `unconverted_count`
    could be dropped from the dict again and the response would quietly fall
    back to its schema default of 0 — a disclosure that says "nothing excluded"
    when rows were."""
    assert set(_vendor_pattern_dict(_pattern())) == set(VendorPatternResponse.model_fields)


def test_a_single_currency_tenant_discloses_nothing():
    payload = _vendor_pattern_dict(_pattern(unconverted_count=0))
    assert payload["unconverted_count"] == 0
