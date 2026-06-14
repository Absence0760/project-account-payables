"""Unit tests for the pure expense-policy engine (``services/expense_policy``).

DB-free: policies + expenses are ``SimpleNamespace`` stand-ins, money is
``Decimal``. Covers category limits, receipt-required, pre-approval-required,
per-diem caps, category matching (NULL = all), the active flag, mileage
reimbursement, report aggregation, and the blocking-subset filter.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

from app.services.expense_policy import (
    VIOLATION_CATEGORY_LIMIT,
    VIOLATION_PER_DIEM_EXCEEDED,
    VIOLATION_PREAPPROVAL_REQUIRED,
    VIOLATION_RECEIPT_REQUIRED,
    blocking_violations,
    evaluate_expense,
    evaluate_report,
    mileage_reimbursement,
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
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _expense(**kw):
    base = dict(
        id=uuid.uuid4(),
        amount=Decimal("100.00"),
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
