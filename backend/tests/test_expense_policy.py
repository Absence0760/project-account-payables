"""Unit tests for the pure expense-policy engine (``services/expense_policy``).

DB-free: policies + expenses are ``SimpleNamespace`` stand-ins, money is
``Decimal``. Covers category limits, receipt-required, pre-approval-required,
per-diem caps, category matching (NULL = all), the active flag, mileage
reimbursement, report aggregation, the blocking-subset filter, and — the
currency dimension — that a threshold is only ever compared against the expense
expressed in the threshold's own currency, failing closed when it cannot be.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

from app.services.expense_policy import (
    COMPARISON_UNRESOLVED,
    VIOLATION_CATEGORY_LIMIT,
    VIOLATION_PER_DIEM_EXCEEDED,
    VIOLATION_PREAPPROVAL_REQUIRED,
    VIOLATION_RECEIPT_REQUIRED,
    blocking_violations,
    evaluate_expense,
    evaluate_report,
    mileage_reimbursement,
    threshold_currency_for,
)


def _policy(**kw):
    base = dict(
        id=uuid.uuid4(),
        active=True,
        category=None,
        per_diem_amount=None,
        mileage_rate=None,
        category_limit=None,
        requires_preapproval_above=None,
        requires_receipt_above=None,
        threshold_currency=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _expense(**kw):
    base = dict(
        id=uuid.uuid4(),
        amount=Decimal("100.00"),
        currency="USD",
        converted_amount=None,
        converted_currency=None,
        category="travel",
        receipt_file_key=None,
        mileage_miles=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


# --- category limit -------------------------------------------------------


def test_category_limit_exceeded():
    pol = _policy(category="travel", category_limit=Decimal("50.00"))
    v = evaluate_expense(_expense(amount=Decimal("75.00")), [pol])
    assert len(v) == 1
    assert v[0]["code"] == VIOLATION_CATEGORY_LIMIT
    assert v[0]["limit"] == "50.00"
    assert v[0]["actual"] == "75.00"


def test_category_limit_not_exceeded():
    pol = _policy(category="travel", category_limit=Decimal("200.00"))
    assert evaluate_expense(_expense(amount=Decimal("75.00")), [pol]) == []


def test_null_category_policy_applies_to_all():
    pol = _policy(category=None, category_limit=Decimal("10.00"))
    v = evaluate_expense(_expense(category="meals", amount=Decimal("25.00")), [pol])
    assert [x["code"] for x in v] == [VIOLATION_CATEGORY_LIMIT]


def test_category_mismatch_skips_policy():
    pol = _policy(category="meals", category_limit=Decimal("1.00"))
    assert evaluate_expense(_expense(category="travel", amount=Decimal("999")), [pol]) == []


def test_inactive_policy_ignored():
    pol = _policy(active=False, category_limit=Decimal("1.00"))
    assert evaluate_expense(_expense(amount=Decimal("999")), [pol]) == []


# --- receipt required (blocking) -----------------------------------------


def test_receipt_required_when_missing():
    pol = _policy(requires_receipt_above=Decimal("25.00"))
    v = evaluate_expense(_expense(amount=Decimal("50.00"), receipt_file_key=None), [pol])
    assert [x["code"] for x in v] == [VIOLATION_RECEIPT_REQUIRED]
    assert blocking_violations(v) == v


def test_receipt_present_clears_violation():
    pol = _policy(requires_receipt_above=Decimal("25.00"))
    v = evaluate_expense(
        _expense(amount=Decimal("50.00"), receipt_file_key="org/expenses/x/r.pdf"), [pol]
    )
    assert v == []


# --- pre-approval required (blocking) ------------------------------------


def test_preapproval_required_when_absent():
    pol = _policy(requires_preapproval_above=Decimal("100.00"))
    v = evaluate_expense(_expense(amount=Decimal("250.00")), [pol])
    assert [x["code"] for x in v] == [VIOLATION_PREAPPROVAL_REQUIRED]
    assert blocking_violations(v) == v


def test_preapproval_satisfied_by_coverage():
    pol = _policy(requires_preapproval_above=Decimal("100.00"))
    v = evaluate_expense(
        _expense(amount=Decimal("250.00")),
        [pol],
        approved_preapproval_amount=Decimal("300.00"),
    )
    assert v == []


def test_preapproval_insufficient_coverage_still_blocks():
    pol = _policy(requires_preapproval_above=Decimal("100.00"))
    v = evaluate_expense(
        _expense(amount=Decimal("250.00")),
        [pol],
        approved_preapproval_amount=Decimal("200.00"),
    )
    assert [x["code"] for x in v] == [VIOLATION_PREAPPROVAL_REQUIRED]


# --- per diem -------------------------------------------------------------


def test_per_diem_exceeded():
    pol = _policy(per_diem_amount=Decimal("60.00"))
    v = evaluate_expense(_expense(amount=Decimal("75.00")), [pol])
    assert [x["code"] for x in v] == [VIOLATION_PER_DIEM_EXCEEDED]


# --- mileage --------------------------------------------------------------


def test_mileage_reimbursement_decimal():
    pol = _policy(mileage_rate=Decimal("0.6700"))
    amount = mileage_reimbursement(_expense(mileage_miles=Decimal("100")), [pol])
    assert amount == Decimal("67.0000")
    assert isinstance(amount, Decimal)


def test_mileage_zero_when_no_rate_or_miles():
    assert mileage_reimbursement(_expense(mileage_miles=None), [_policy()]) == Decimal("0")
    assert mileage_reimbursement(_expense(mileage_miles=Decimal("50")), []) == Decimal("0")


# --- report aggregation + blocking filter --------------------------------


def test_evaluate_report_aggregates_and_tags_expense_id():
    pol = _policy(requires_receipt_above=Decimal("10.00"), category_limit=Decimal("20.00"))
    e1 = _expense(amount=Decimal("50.00"), receipt_file_key=None)
    e2 = _expense(amount=Decimal("5.00"), receipt_file_key="k")
    report = SimpleNamespace(id=uuid.uuid4())
    agg = evaluate_report(report, [e1, e2], [pol])
    # e1 trips both receipt + category-limit; e2 trips nothing.
    codes = {(x["expense_id"], x["code"]) for x in agg}
    assert (str(e1.id), VIOLATION_RECEIPT_REQUIRED) in codes
    assert (str(e1.id), VIOLATION_CATEGORY_LIMIT) in codes
    assert all(x["expense_id"] == str(e1.id) for x in agg)

    blocking = blocking_violations(agg)
    assert {x["code"] for x in blocking} == {VIOLATION_RECEIPT_REQUIRED}


def test_clean_expense_no_violations():
    pol = _policy(category_limit=Decimal("1000.00"), per_diem_amount=Decimal("1000.00"))
    assert evaluate_expense(_expense(amount=Decimal("100.00")), [pol]) == []


# --- currency awareness ---------------------------------------------------
#
# The defect these cover: every threshold was compared to ``expense.amount`` as
# a bare number, so a €200 EUR expense was judged against a USD 100 limit as
# "200 > 100". ``receipt_required`` is BLOCKING, so the wrong answer could block
# a compliant expense or wave a non-compliant one through.


def test_threshold_currency_for_prefers_policy_then_default():
    assert threshold_currency_for(_policy(threshold_currency="eur"), "USD") == "EUR"
    assert threshold_currency_for(_policy(threshold_currency=None), "GBP") == "GBP"
    assert threshold_currency_for(_policy(threshold_currency="  "), "GBP") == "GBP"
    # A policy stand-in without the attribute at all must not explode.
    assert threshold_currency_for(SimpleNamespace(), "JPY") == "JPY"


def test_eur_expense_is_not_compared_to_a_usd_limit_as_bare_numbers():
    """THE defect. €200 vs a USD 100 limit, with no rate on the row.

    Bare-number logic answers "200 > 100 → over the limit". That verdict is
    meaningless, so the engine must not report it as a performed comparison: it
    flags for review, tagged unresolved, naming both currencies."""
    pol = _policy(threshold_currency="USD", category_limit=Decimal("100.00"))
    exp = _expense(amount=Decimal("200.00"), currency="EUR")
    v = evaluate_expense(exp, [pol], default_threshold_currency="USD")
    assert [x["code"] for x in v] == [VIOLATION_CATEGORY_LIMIT]
    assert v[0]["comparison"] == COMPARISON_UNRESOLVED
    assert v[0]["currency"] == "USD"  # the unit of `limit`
    assert v[0]["expense_currency"] == "EUR"  # the unit of `actual`
    assert v[0]["limit"] == "100.00"
    assert v[0]["actual"] == "200.00"


def test_locked_conversion_below_the_limit_clears_it():
    """The other half of the same defect, and the one bare numbers get wrong in
    the dangerous-for-the-employee direction: ¥10 000 JPY is $64.94 — under a
    USD 100 limit — yet "10000 > 100" would flag it."""
    pol = _policy(threshold_currency="USD", category_limit=Decimal("100.00"))
    exp = _expense(
        amount=Decimal("10000.00"),
        currency="JPY",
        converted_amount=Decimal("64.94"),
        converted_currency="USD",
    )
    assert evaluate_expense(exp, [pol], default_threshold_currency="USD") == []


def test_locked_conversion_above_the_limit_reports_the_converted_figure():
    pol = _policy(threshold_currency="USD", category_limit=Decimal("100.00"))
    exp = _expense(
        amount=Decimal("200.00"),
        currency="EUR",
        converted_amount=Decimal("217.39"),
        converted_currency="USD",
    )
    v = evaluate_expense(exp, [pol], default_threshold_currency="USD")
    assert [x["code"] for x in v] == [VIOLATION_CATEGORY_LIMIT]
    assert "comparison" not in v[0]  # a real comparison happened
    assert v[0]["actual"] == "217.39"
    assert v[0]["currency"] == "USD"


def test_conversion_into_a_third_currency_is_not_reused():
    """A line locked into its REPORT's currency (GBP) says nothing about a
    threshold denominated in EUR — that is still unresolved, not face value."""
    pol = _policy(threshold_currency="EUR", category_limit=Decimal("100.00"))
    exp = _expense(
        amount=Decimal("50.00"),
        currency="USD",
        converted_amount=Decimal("39.50"),
        converted_currency="GBP",
    )
    v = evaluate_expense(exp, [pol], default_threshold_currency="EUR")
    assert [x["code"] for x in v] == [VIOLATION_CATEGORY_LIMIT]
    assert v[0]["comparison"] == COMPARISON_UNRESOLVED


def test_policy_without_currency_uses_the_org_reporting_currency():
    """A legacy row (threshold_currency NULL) is read in the org's reporting
    currency — so a EUR org's EUR expense compares directly, no FX needed."""
    pol = _policy(threshold_currency=None, category_limit=Decimal("100.00"))
    over = evaluate_expense(
        _expense(amount=Decimal("150.00"), currency="EUR"),
        [pol],
        default_threshold_currency="EUR",
    )
    assert [x["code"] for x in over] == [VIOLATION_CATEGORY_LIMIT]
    assert over[0]["currency"] == "EUR"
    assert "comparison" not in over[0]
    # ...and the same expense against a USD-reporting org is unresolvable.
    unresolved = evaluate_expense(
        _expense(amount=Decimal("150.00"), currency="EUR"),
        [pol],
        default_threshold_currency="USD",
    )
    assert unresolved[0]["comparison"] == COMPARISON_UNRESOLVED


# --- fail-closed behaviour of each threshold ------------------------------


def test_receipt_required_fails_closed_when_unresolvable():
    """BLOCKING. ¥10 000 is well under a USD 5 000 receipt threshold once
    converted, but with no rate on the row the engine cannot know that — so it
    demands the receipt rather than waving the expense through."""
    pol = _policy(threshold_currency="USD", requires_receipt_above=Decimal("5000.00"))
    v = evaluate_expense(
        _expense(amount=Decimal("10000.00"), currency="JPY", receipt_file_key=None),
        [pol],
        default_threshold_currency="USD",
    )
    assert [x["code"] for x in v] == [VIOLATION_RECEIPT_REQUIRED]
    assert v[0]["comparison"] == COMPARISON_UNRESOLVED
    assert blocking_violations(v) == v


def test_receipt_present_clears_the_unresolvable_case():
    """The receipt is evidence that does not depend on the rate, so it still
    satisfies the rule — failing closed must not become un-satisfiable."""
    pol = _policy(threshold_currency="USD", requires_receipt_above=Decimal("5000.00"))
    v = evaluate_expense(
        _expense(amount=Decimal("10000.00"), currency="JPY", receipt_file_key="k/r.pdf"),
        [pol],
        default_threshold_currency="USD",
    )
    assert v == []


def test_preapproval_fails_closed_when_unresolvable_and_uncovered():
    pol = _policy(threshold_currency="USD", requires_preapproval_above=Decimal("5000.00"))
    v = evaluate_expense(
        _expense(amount=Decimal("10000.00"), currency="JPY"),
        [pol],
        default_threshold_currency="USD",
    )
    assert [x["code"] for x in v] == [VIOLATION_PREAPPROVAL_REQUIRED]
    assert v[0]["comparison"] == COMPARISON_UNRESOLVED
    assert blocking_violations(v) == v


def test_preapproval_coverage_in_the_expense_currency_still_satisfies():
    """The caller currency-matches the pre-approval to the expense, so the
    coverage check stands on its own even with the threshold unresolvable."""
    pol = _policy(threshold_currency="USD", requires_preapproval_above=Decimal("5000.00"))
    v = evaluate_expense(
        _expense(amount=Decimal("10000.00"), currency="JPY"),
        [pol],
        approved_preapproval_amount=Decimal("12000.00"),
        default_threshold_currency="USD",
    )
    assert v == []


def test_per_diem_fails_closed_when_unresolvable():
    pol = _policy(threshold_currency="USD", per_diem_amount=Decimal("60.00"))
    v = evaluate_expense(
        _expense(amount=Decimal("40.00"), currency="EUR"),
        [pol],
        default_threshold_currency="USD",
    )
    assert [x["code"] for x in v] == [VIOLATION_PER_DIEM_EXCEEDED]
    assert v[0]["comparison"] == COMPARISON_UNRESOLVED
    # Advisory, not blocking — it never stops a submission.
    assert blocking_violations(v) == []


def test_violation_payload_carries_no_pii():
    """Messages + payloads are amounts and ISO codes only — no merchant, no
    person, no description (they land in an API body and a JSONB column)."""
    pol = _policy(threshold_currency="USD", category_limit=Decimal("10.00"))
    exp = _expense(
        amount=Decimal("100.00"),
        currency="EUR",
        merchant="Hotel Zurück, Herrn K. Meier",
        description="dinner with the CFO",
    )
    v = evaluate_expense(exp, [pol], default_threshold_currency="USD")
    blob = repr(v)
    assert "Hotel" not in blob and "Meier" not in blob and "CFO" not in blob


def test_evaluate_report_forwards_the_default_threshold_currency():
    pol = _policy(threshold_currency=None, category_limit=Decimal("100.00"))
    e1 = _expense(amount=Decimal("150.00"), currency="EUR")
    agg = evaluate_report(
        SimpleNamespace(id=uuid.uuid4()), [e1], [pol], default_threshold_currency="EUR"
    )
    assert [x["code"] for x in agg] == [VIOLATION_CATEGORY_LIMIT]
    assert agg[0]["currency"] == "EUR"
    assert "comparison" not in agg[0]
