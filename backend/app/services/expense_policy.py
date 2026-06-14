"""Expense policy engine — pure, deterministic, no LLM, no DB, no network.

Given an ``Expense`` (or a report's expenses) and the set of active
``ExpensePolicy`` rows, derive the list of policy violations. The engine itself
does **no** I/O: the caller (``api/expenses.py`` / the report-submit route)
loads the active policies (and any approved pre-approvals) from the tenant DB
and hands them in, so this module stays trivially unit-testable.

Money is ``Decimal`` throughout — never ``float``. Mileage reimbursement,
per-diem caps, and category limits all compare ``Decimal`` to ``Decimal``.

A violation is a small JSON-serialisable dict::

    {"code": "category_limit", "message": "...", "policy_id": "<uuid>",
     "limit": "100.00", "actual": "150.00"}

``code`` is one of the module-level ``VIOLATION_*`` constants. ``BLOCKING_CODES``
names the subset that blocks report submission (missing required receipt, or a
required pre-approval that is absent / not approved). Everything else is
advisory and surfaces as a warning badge.

See ``backend/docs/expense-management.md``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

# Violation codes ----------------------------------------------------------
VIOLATION_CATEGORY_LIMIT = "category_limit"
VIOLATION_RECEIPT_REQUIRED = "receipt_required"
VIOLATION_PREAPPROVAL_REQUIRED = "preapproval_required"
VIOLATION_PER_DIEM_EXCEEDED = "per_diem_exceeded"

# The subset that blocks report submission. The rest are advisory.
BLOCKING_CODES = frozenset({VIOLATION_RECEIPT_REQUIRED, VIOLATION_PREAPPROVAL_REQUIRED})


def _as_decimal(value: Any) -> Decimal | None:
    """Coerce a money-ish value to ``Decimal`` (via ``str`` for float safety).

    Returns ``None`` for ``None`` / unparseable input so callers can treat a
    missing limit as "no rule"."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _policy_applies(policy: Any, category: str | None) -> bool:
    """A policy applies to an expense when the policy is active AND its category
    is NULL (applies to all) or matches the expense's category."""
    if not getattr(policy, "active", False):
        return False
    pol_cat = getattr(policy, "category", None)
    return pol_cat is None or pol_cat == category


def mileage_reimbursement(expense: Any, policies: list[Any]) -> Decimal:
    """Reimbursable mileage amount = ``mileage_miles * mileage_rate``.

    The rate comes from the first applicable active policy that carries a
    ``mileage_rate``. Returns ``Decimal("0")`` when the expense logs no miles or
    no policy sets a rate. Pure ``Decimal`` math (no float)."""
    miles = _as_decimal(getattr(expense, "mileage_miles", None))
    if miles is None or miles <= 0:
        return Decimal("0")
    category = getattr(expense, "category", None)
    for policy in policies:
        if not _policy_applies(policy, category):
            continue
        rate = _as_decimal(getattr(policy, "mileage_rate", None))
        if rate is not None:
            return miles * rate
    return Decimal("0")


def evaluate_expense(
    expense: Any,
    policies: list[Any],
    *,
    approved_preapproval_amount: Decimal | None = None,
) -> list[dict]:
    """Evaluate a single expense against the active policies.

    Returns a list of violation dicts (empty when clean). Rules, per applicable
    policy:

    - ``category_limit``: ``amount`` exceeds the policy's ``category_limit``.
    - ``receipt_required``: ``amount`` exceeds ``requires_receipt_above`` and no
      ``receipt_file_key`` is present (BLOCKING).
    - ``preapproval_required``: ``amount`` exceeds ``requires_preapproval_above``
      and no approved pre-approval covers it (BLOCKING). ``approved_preapproval_amount``
      is the estimated amount of a linked approved pre-approval (the caller
      resolves it from the DB); when present and >= the expense amount the rule
      is satisfied.
    - ``per_diem_exceeded``: ``amount`` exceeds the policy's ``per_diem_amount``.

    All comparisons are ``Decimal``. Best-effort: an unparseable amount yields no
    violations rather than raising (callers wrap this anyway)."""
    amount = _as_decimal(getattr(expense, "amount", None))
    if amount is None:
        return []
    category = getattr(expense, "category", None)
    receipt_key = getattr(expense, "receipt_file_key", None)
    covered = _as_decimal(approved_preapproval_amount)

    violations: list[dict] = []
    for policy in policies:
        if not _policy_applies(policy, category):
            continue
        policy_id = str(getattr(policy, "id", "")) or None

        cat_limit = _as_decimal(getattr(policy, "category_limit", None))
        if cat_limit is not None and amount > cat_limit:
            violations.append(
                {
                    "code": VIOLATION_CATEGORY_LIMIT,
                    "message": (
                        f"Amount {amount} exceeds the {category or 'category'} "
                        f"limit of {cat_limit}."
                    ),
                    "policy_id": policy_id,
                    "limit": str(cat_limit),
                    "actual": str(amount),
                }
            )

        receipt_above = _as_decimal(getattr(policy, "requires_receipt_above", None))
        if receipt_above is not None and amount > receipt_above and not receipt_key:
            violations.append(
                {
                    "code": VIOLATION_RECEIPT_REQUIRED,
                    "message": (f"A receipt is required for amounts above {receipt_above}."),
                    "policy_id": policy_id,
                    "limit": str(receipt_above),
                    "actual": str(amount),
                }
            )

        preapproval_above = _as_decimal(getattr(policy, "requires_preapproval_above", None))
        if preapproval_above is not None and amount > preapproval_above:
            satisfied = covered is not None and covered >= amount
            if not satisfied:
                violations.append(
                    {
                        "code": VIOLATION_PREAPPROVAL_REQUIRED,
                        "message": (
                            f"Pre-approval is required for amounts above {preapproval_above}."
                        ),
                        "policy_id": policy_id,
                        "limit": str(preapproval_above),
                        "actual": str(amount),
                    }
                )

        per_diem = _as_decimal(getattr(policy, "per_diem_amount", None))
        if per_diem is not None and amount > per_diem:
            violations.append(
                {
                    "code": VIOLATION_PER_DIEM_EXCEEDED,
                    "message": (f"Amount {amount} exceeds the per-diem cap of {per_diem}."),
                    "policy_id": policy_id,
                    "limit": str(per_diem),
                    "actual": str(amount),
                }
            )

    return violations


def evaluate_report(
    report: Any,
    expenses: list[Any],
    policies: list[Any],
    *,
    preapproval_amount_by_expense: dict | None = None,
) -> list[dict]:
    """Aggregate per-expense violations across a report.

    Returns a flat list of violation dicts, each annotated with the source
    ``expense_id`` so the caller can map a blocking violation back to a line.
    ``preapproval_amount_by_expense`` optionally maps an expense id (str) to the
    estimated amount of an approved pre-approval covering it."""
    cover = preapproval_amount_by_expense or {}
    aggregate: list[dict] = []
    for expense in expenses:
        eid = str(getattr(expense, "id", "")) or None
        per_expense = evaluate_expense(
            expense,
            policies,
            approved_preapproval_amount=cover.get(eid),
        )
        for violation in per_expense:
            entry = dict(violation)
            entry["expense_id"] = eid
            aggregate.append(entry)
    return aggregate


def blocking_violations(violations: list[dict]) -> list[dict]:
    """Filter a violation list to the submission-blocking subset."""
    return [v for v in violations if v.get("code") in BLOCKING_CODES]
