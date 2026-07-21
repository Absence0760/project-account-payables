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

Every comparison is **currency-aware**. A policy's money thresholds
(``category_limit`` / ``per_diem_amount`` / ``requires_receipt_above`` /
``requires_preapproval_above``) are denominated in the policy's
``threshold_currency``, and the expense is expressed in that currency before
being compared — a €200 EUR expense is never judged against a USD 100 limit as
bare numbers. ``threshold_currency`` is ``NULL`` on rows that predate the
column; the caller then supplies the org's **reporting currency** as
``default_threshold_currency`` (that is the unit the whole app already assumes
for a bare number — the CFO expense threshold, the policy table in the UI).

No FX call happens here: this engine is pure. The expense is expressed in the
threshold currency using only what a write path already locked
(``expense_currency.expense_amount_in_currency``). When that yields ``None`` —
a foreign-currency expense with no lock into the threshold currency — the
comparison genuinely **cannot** be made, and every rule then **fails closed**:
the violation is raised anyway, tagged ``comparison: "unresolved"``. A blocking
rule therefore demands the receipt / pre-approval instead of waving the expense
through on a comparison that was never performed.

See ``backend/docs/expense-management.md``.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.expense_currency import expense_amount_in_currency, normalize_currency

# Violation codes ----------------------------------------------------------
VIOLATION_CATEGORY_LIMIT = "category_limit"
VIOLATION_RECEIPT_REQUIRED = "receipt_required"
VIOLATION_PREAPPROVAL_REQUIRED = "preapproval_required"
VIOLATION_PER_DIEM_EXCEEDED = "per_diem_exceeded"

# The subset that blocks report submission. The rest are advisory.
BLOCKING_CODES = frozenset({VIOLATION_RECEIPT_REQUIRED, VIOLATION_PREAPPROVAL_REQUIRED})

# Marker on a violation raised without a performed comparison (see the module
# docstring): the expense could not be expressed in the threshold's currency, so
# the rule fell closed. Surfaced so the UI can explain the flag honestly.
COMPARISON_UNRESOLVED = "unresolved"


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


def threshold_currency_for(policy: Any, default_currency: str) -> str:
    """The currency a policy's money thresholds are denominated in.

    ``ExpensePolicy.threshold_currency`` when the admin has set one; otherwise
    ``default_currency``, which callers resolve as the org's reporting currency
    (``currency_conversion.resolve_reporting_currency``). ``NULL`` is the state
    of every row created before the column existed: a tenant-DB migration cannot
    read the control-plane org settings, so the unit is resolved here, at
    evaluation time, from live config rather than frozen to a guess at upgrade
    time. Never raises."""
    return normalize_currency(getattr(policy, "threshold_currency", None), default=default_currency)


def mileage_reimbursement(expense: Any, policies: list[Any]) -> Decimal:
    """Reimbursable mileage amount = ``mileage_miles * mileage_rate``.

    The rate comes from the first applicable active policy that carries a
    ``mileage_rate``, and the result is denominated in that policy's
    ``threshold_currency``. Returns ``Decimal("0")`` when the expense logs no
    miles or no policy sets a rate. Pure ``Decimal`` math (no float)."""
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


def _violation(
    code: str,
    *,
    message: str,
    policy_id: str | None,
    limit: Decimal,
    actual: Decimal,
    currency: str,
    expense_currency: str,
    unresolved: bool,
) -> dict:
    """Build one violation dict. PII-free by construction — amounts and ISO
    currency codes only, never a merchant, a person, or a bank detail."""
    entry = {
        "code": code,
        "message": message,
        "policy_id": policy_id,
        "limit": str(limit),
        "actual": str(actual),
        # The unit BOTH ``limit`` and (when resolved) ``actual`` are in.
        "currency": currency,
    }
    if unresolved:
        # ``actual`` is the expense's own face amount here, in its own currency —
        # no conversion existed to express it in ``currency``.
        entry["comparison"] = COMPARISON_UNRESOLVED
        entry["expense_currency"] = expense_currency
    return entry


def evaluate_expense(
    expense: Any,
    policies: list[Any],
    *,
    approved_preapproval_amount: Decimal | None = None,
    default_threshold_currency: str = "USD",
) -> list[dict]:
    """Evaluate a single expense against the active policies.

    Returns a list of violation dicts (empty when clean). Every money rule
    compares the expense **in the policy's threshold currency**
    (``threshold_currency_for``), using only a conversion a write path already
    locked onto the row — never a fresh rate. Rules, per applicable policy:

    - ``category_limit``: the expense exceeds the policy's ``category_limit``.
    - ``receipt_required``: the expense exceeds ``requires_receipt_above`` and no
      ``receipt_file_key`` is present (BLOCKING).
    - ``preapproval_required``: the expense exceeds ``requires_preapproval_above``
      and no approved pre-approval covers it (BLOCKING). ``approved_preapproval_amount``
      is the estimated amount of a linked approved pre-approval (the caller
      resolves it from the DB, currency-matched to the expense); when present and
      >= the expense amount the rule is satisfied.
    - ``per_diem_exceeded``: the expense exceeds the policy's ``per_diem_amount``.

    ``default_threshold_currency`` is the unit for policies with no
    ``threshold_currency`` set — callers pass the org's reporting currency.

    **Fail closed.** When the expense cannot be expressed in a policy's threshold
    currency, that policy's rules are raised anyway, tagged
    ``comparison: "unresolved"``: a receipt / pre-approval is *demanded* rather
    than waived on a comparison that never happened, and the advisory limits flag
    for a human instead of silently passing. The one thing that still clears a
    blocking rule is evidence that does not depend on the rate — an attached
    receipt, or a pre-approval covering the expense in the expense's own currency.

    All comparisons are ``Decimal``. Best-effort: an unparseable amount yields no
    violations rather than raising (callers wrap this anyway)."""
    amount = _as_decimal(getattr(expense, "amount", None))
    if amount is None:
        return []
    category = getattr(expense, "category", None)
    receipt_key = getattr(expense, "receipt_file_key", None)
    covered = _as_decimal(approved_preapproval_amount)
    expense_currency = normalize_currency(
        getattr(expense, "currency", None), default=default_threshold_currency
    )

    violations: list[dict] = []
    for policy in policies:
        if not _policy_applies(policy, category):
            continue
        policy_id = str(getattr(policy, "id", "")) or None
        tcur = threshold_currency_for(policy, default_threshold_currency)
        comparable = expense_amount_in_currency(expense, target_currency=tcur)
        # Each rule's breach test below reads ``unresolved or comparable > limit``:
        # a comparison that cannot be made counts as a breach (fail closed).
        unresolved = comparable is None
        # ``actual`` reports the figure that was (or would have been) compared —
        # the converted figure, or the face amount when nothing could convert it.
        actual = amount if unresolved else comparable

        cat_limit = _as_decimal(getattr(policy, "category_limit", None))
        if cat_limit is not None and (unresolved or comparable > cat_limit):
            label = category or "category"
            message = (
                f"Amount {amount} {expense_currency} cannot be converted to {tcur} to check "
                f"the {label} limit of {cat_limit} {tcur}; flagged for review."
                if unresolved
                else f"Amount {actual} {tcur} exceeds the {label} limit of {cat_limit} {tcur}."
            )
            violations.append(
                _violation(
                    VIOLATION_CATEGORY_LIMIT,
                    message=message,
                    policy_id=policy_id,
                    limit=cat_limit,
                    actual=actual,
                    currency=tcur,
                    expense_currency=expense_currency,
                    unresolved=unresolved,
                )
            )

        receipt_above = _as_decimal(getattr(policy, "requires_receipt_above", None))
        if (
            receipt_above is not None
            and (unresolved or comparable > receipt_above)
            and not receipt_key
        ):
            message = (
                f"A receipt is required: amount {amount} {expense_currency} cannot be converted "
                f"to {tcur} to test the {receipt_above} {tcur} receipt threshold."
                if unresolved
                else f"A receipt is required for amounts above {receipt_above} {tcur}."
            )
            violations.append(
                _violation(
                    VIOLATION_RECEIPT_REQUIRED,
                    message=message,
                    policy_id=policy_id,
                    limit=receipt_above,
                    actual=actual,
                    currency=tcur,
                    expense_currency=expense_currency,
                    unresolved=unresolved,
                )
            )

        preapproval_above = _as_decimal(getattr(policy, "requires_preapproval_above", None))
        if preapproval_above is not None and (unresolved or comparable > preapproval_above):
            # ``covered`` is currency-matched to the expense by the caller, so
            # this check stands on its own even when the threshold is unresolved.
            satisfied = covered is not None and covered >= amount
            if not satisfied:
                message = (
                    f"Pre-approval is required: amount {amount} {expense_currency} cannot be "
                    f"converted to {tcur} to test the {preapproval_above} {tcur} threshold."
                    if unresolved
                    else f"Pre-approval is required for amounts above {preapproval_above} {tcur}."
                )
                violations.append(
                    _violation(
                        VIOLATION_PREAPPROVAL_REQUIRED,
                        message=message,
                        policy_id=policy_id,
                        limit=preapproval_above,
                        actual=actual,
                        currency=tcur,
                        expense_currency=expense_currency,
                        unresolved=unresolved,
                    )
                )

        per_diem = _as_decimal(getattr(policy, "per_diem_amount", None))
        if per_diem is not None and (unresolved or comparable > per_diem):
            message = (
                f"Amount {amount} {expense_currency} cannot be converted to {tcur} to check "
                f"the per-diem cap of {per_diem} {tcur}; flagged for review."
                if unresolved
                else f"Amount {actual} {tcur} exceeds the per-diem cap of {per_diem} {tcur}."
            )
            violations.append(
                _violation(
                    VIOLATION_PER_DIEM_EXCEEDED,
                    message=message,
                    policy_id=policy_id,
                    limit=per_diem,
                    actual=actual,
                    currency=tcur,
                    expense_currency=expense_currency,
                    unresolved=unresolved,
                )
            )

    return violations


def evaluate_report(
    report: Any,
    expenses: list[Any],
    policies: list[Any],
    *,
    preapproval_amount_by_expense: dict | None = None,
    default_threshold_currency: str = "USD",
) -> list[dict]:
    """Aggregate per-expense violations across a report.

    Returns a flat list of violation dicts, each annotated with the source
    ``expense_id`` so the caller can map a blocking violation back to a line.
    ``preapproval_amount_by_expense`` optionally maps an expense id (str) to the
    estimated amount of an approved pre-approval covering it.
    ``default_threshold_currency`` is forwarded to ``evaluate_expense``."""
    cover = preapproval_amount_by_expense or {}
    aggregate: list[dict] = []
    for expense in expenses:
        eid = str(getattr(expense, "id", "")) or None
        per_expense = evaluate_expense(
            expense,
            policies,
            approved_preapproval_amount=cover.get(eid),
            default_threshold_currency=default_threshold_currency,
        )
        for violation in per_expense:
            entry = dict(violation)
            entry["expense_id"] = eid
            aggregate.append(entry)
    return aggregate


def blocking_violations(violations: list[dict]) -> list[dict]:
    """Filter a violation list to the submission-blocking subset."""
    return [v for v in violations if v.get("code") in BLOCKING_CODES]
