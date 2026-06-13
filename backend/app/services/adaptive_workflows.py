"""Adaptive AI workflows — pure computation over approval history.

This is the **read-model + anomaly-baseline + suggestion-derivation** math for
the "Adaptive AI Workflows" slice. Every function here is sync + pure (no async,
no IO): the caller (``app/api/adaptive_workflows.py``) pulls the rows from the
tenant DB and hands them in already shaped, so the statistics are unit-testable
without a database and — critically — **deterministic** (no LLM, no cloud key;
mirrors the local-first invariant). All amount math is ``Decimal``.

Three concerns, one module:

  1. **Approval-pattern learning** — per-approver and per-vendor aggregates over
     the tenant's approval/rejection history (``compute_approver_patterns`` /
     ``compute_vendor_patterns``).
  2. **Anomaly detection** — build a per-vendor *baseline* from that vendor's
     historically-approved invoices (``compute_vendor_baseline``) and flag a
     single invoice against it on three axes — amount / approver / timing
     (``detect_invoice_anomaly``). Read-only and **explainable**: the baseline
     it compared against is returned alongside the flags.
  3. **Suggestion derivation** — turn the vendor patterns into advisory
     "consider auto-approve under $X" suggestions (``derive_suggestions``). The
     suggestions are inert advisory data — nothing here applies anything.

Relationship to existing code (see ``backend/docs/adaptive-workflows.md``):
``invoice_warnings.fraud_stat_anomaly`` already does a *per-invoice,
single-vendor* σ check that **writes** warnings + Exception rows. This module's
anomaly surface is **read-only**, on-demand, covers three axes, and returns the
baseline — it does NOT write warnings or Exceptions. The two are complementary.

The ``_avg`` / ``_quantile`` / ``_decimal_days`` helpers are imported from
``app.services.analytics`` rather than re-implemented; ``_stdev`` is added here
because analytics doesn't expose one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from app.services.analytics import _avg, _decimal_days, _quantile  # noqa: F401

__all__ = [
    "ApproverPattern",
    "VendorApprovalPattern",
    "ApprovalPatterns",
    "VendorBaseline",
    "AnomalyFlag",
    "InvoiceAnomaly",
    "DerivedSuggestion",
    "compute_approver_patterns",
    "compute_vendor_patterns",
    "compute_vendor_baseline",
    "detect_invoice_anomaly",
    "derive_suggestions",
    "_decimal_days",
]

_CENTS = Decimal("0.01")
_TENTH = Decimal("0.1")


def _q2(x: Decimal) -> Decimal:
    return x.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _q1(x: Decimal) -> Decimal:
    return x.quantize(_TENTH, rounding=ROUND_HALF_UP)


def _stdev(values: list[Decimal]) -> Decimal:
    """Population standard deviation, Decimal. <2 samples → 0."""
    n = len(values)
    if n < 2:
        return Decimal("0")
    mean = sum(values, Decimal("0")) / Decimal(n)
    var = sum(((v - mean) ** 2 for v in values), Decimal("0")) / Decimal(n)
    return var.sqrt()  # Decimal.sqrt — exact-context square root


def _get(row, attr, default=None):
    """Duck-typed access: dict key or attribute."""
    if isinstance(row, dict):
        return row.get(attr, default)
    return getattr(row, attr, default)


# ---------------------------------------------------------------------------
# Approval-pattern dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApproverPattern:
    approver_id: str  # UUID str, or "unknown" when actor_id is NULL
    approver_name: str | None  # joined from control-plane User; may be None
    approved_count: int
    rejected_count: int
    approval_rate_pct: Decimal  # approved / (approved+rejected) * 100, 0.1
    median_time_to_approve_days: Decimal  # over approved-leg samples
    avg_time_to_approve_days: Decimal
    sample_size: int  # approved+rejected (decisions)


@dataclass(frozen=True)
class VendorApprovalPattern:
    vendor_id: str | None  # UUID str, None when invoice had no vendor link
    vendor_name: str
    approved_count: int
    rejected_count: int
    approval_rate_pct: Decimal
    unmodified_count: int  # approved with NO corrections (details.changes absent)
    consistency_pct: Decimal  # unmodified_count / approved_count * 100, 0.1
    avg_approved_amount: Decimal  # Numeric mean, 0.01
    median_approved_amount: Decimal
    min_approved_amount: Decimal
    max_approved_amount: Decimal
    sample_size: int  # approved+rejected


@dataclass(frozen=True)
class ApprovalPatterns:
    approvers: list[ApproverPattern]
    vendors: list[VendorApprovalPattern]
    generated_at: datetime  # UTC, set by API layer (pass now in)


def _rate_pct(approved: int, rejected: int) -> Decimal:
    total = approved + rejected
    if total == 0:
        return Decimal("0")
    return _q1(Decimal(approved) / Decimal(total) * Decimal("100"))


def compute_approver_patterns(
    decisions: list, *, names: dict[str, str] | None = None
) -> list[ApproverPattern]:
    """Aggregate per-approver approval behaviour from decision rows.

    A decision row is duck-typed (dict or object) with: ``approver_id`` (str |
    None), ``decision`` ("approved" | "rejected"), and ``time_to_approve_days``
    (Decimal | None — None for rejections). ``names`` maps approver id →
    display name.
    """
    names = names or {}
    buckets: dict[str, dict] = {}
    for row in decisions:
        approver = _get(row, "approver_id")
        key = str(approver) if approver else "unknown"
        b = buckets.setdefault(key, {"approved": 0, "rejected": 0, "times": []})
        decision = _get(row, "decision")
        if decision == "approved":
            b["approved"] += 1
            ttd = _get(row, "time_to_approve_days")
            if ttd is not None:
                b["times"].append(ttd)
        elif decision == "rejected":
            b["rejected"] += 1

    out: list[ApproverPattern] = []
    for key, b in buckets.items():
        approved, rejected, times = b["approved"], b["rejected"], b["times"]
        out.append(
            ApproverPattern(
                approver_id=key,
                approver_name=names.get(key),
                approved_count=approved,
                rejected_count=rejected,
                approval_rate_pct=_rate_pct(approved, rejected),
                median_time_to_approve_days=_quantile(times, 0.5) if times else Decimal("0"),
                avg_time_to_approve_days=_avg(times) if times else Decimal("0"),
                sample_size=approved + rejected,
            )
        )
    out.sort(key=lambda p: -p.sample_size)
    return out


def compute_vendor_patterns(decisions: list) -> list[VendorApprovalPattern]:
    """Aggregate per-vendor approval behaviour from decision rows.

    A decision row adds ``vendor_id`` (str | None), ``vendor_name`` (str),
    ``amount`` (Decimal), ``unmodified`` (bool) to the approver-row shape.
    """
    buckets: dict[str, dict] = {}
    for row in decisions:
        vendor_id = _get(row, "vendor_id")
        vendor_name = _get(row, "vendor_name") or ""
        key = str(vendor_id) if vendor_id else f"__name__:{vendor_name}"
        b = buckets.setdefault(
            key,
            {
                "vendor_id": str(vendor_id) if vendor_id else None,
                "vendor_name": vendor_name,
                "approved": 0,
                "rejected": 0,
                "unmodified": 0,
                "amounts": [],
            },
        )
        decision = _get(row, "decision")
        if decision == "approved":
            b["approved"] += 1
            if _get(row, "unmodified"):
                b["unmodified"] += 1
            amount = _get(row, "amount")
            if amount is not None:
                b["amounts"].append(Decimal(str(amount)))
        elif decision == "rejected":
            b["rejected"] += 1

    out: list[VendorApprovalPattern] = []
    for b in buckets.values():
        approved, rejected = b["approved"], b["rejected"]
        unmodified = b["unmodified"]
        amounts = b["amounts"]
        consistency = (
            _q1(Decimal(unmodified) / Decimal(approved) * Decimal("100"))
            if approved
            else Decimal("0")
        )
        if amounts:
            avg_amt = _q2(sum(amounts, Decimal("0")) / Decimal(len(amounts)))
            median_amt = _q2(_quantile(amounts, 0.5))
            min_amt = _q2(min(amounts))
            max_amt = _q2(max(amounts))
        else:
            avg_amt = median_amt = min_amt = max_amt = Decimal("0.00")
        out.append(
            VendorApprovalPattern(
                vendor_id=b["vendor_id"],
                vendor_name=b["vendor_name"],
                approved_count=approved,
                rejected_count=rejected,
                approval_rate_pct=_rate_pct(approved, rejected),
                unmodified_count=unmodified,
                consistency_pct=consistency,
                avg_approved_amount=avg_amt,
                median_approved_amount=median_amt,
                min_approved_amount=min_amt,
                max_approved_amount=max_amt,
                sample_size=approved + rejected,
            )
        )
    out.sort(key=lambda p: (-p.sample_size, p.vendor_name))
    return out


# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VendorBaseline:
    vendor_id: str | None
    vendor_name: str
    sample_size: int  # # approved invoices in baseline
    mean_amount: Decimal
    median_amount: Decimal
    stdev_amount: Decimal
    min_amount: Decimal
    max_amount: Decimal
    typical_approver_ids: list[str]  # approvers who approved this vendor before
    median_time_to_approve_days: Decimal


@dataclass(frozen=True)
class AnomalyFlag:
    code: str  # "amount_high" | "amount_low" | "unusual_approver" | "off_pattern_timing"
    severity: str  # "info" | "warning"
    message: str  # templated, deterministic
    observed: str  # string-Decimal or value observed
    expected: str  # the baseline bound it breached (string)


@dataclass(frozen=True)
class InvoiceAnomaly:
    invoice_id: str
    vendor_id: str | None
    vendor_name: str
    amount: Decimal
    baseline: VendorBaseline | None  # None when insufficient history
    flags: list[AnomalyFlag]
    insufficient_history: bool


def compute_vendor_baseline(
    approved_rows: list,
    *,
    vendor_id: str | None = None,
    vendor_name: str = "",
    min_history: int = 5,
) -> VendorBaseline | None:
    """Build a per-vendor baseline from that vendor's approved invoices.

    ``approved_rows`` are duck-typed rows with ``amount`` (Decimal),
    ``approver_id`` (str | None), ``time_to_approve_days`` (Decimal | None).
    Returns ``None`` when there's less than ``min_history`` approved invoices —
    too thin a base to call anything anomalous.
    """
    if len(approved_rows) < min_history:
        return None
    amounts = [Decimal(str(_get(r, "amount") or 0)) for r in approved_rows]
    times = [t for r in approved_rows if (t := _get(r, "time_to_approve_days")) is not None]
    approvers: list[str] = []
    seen: set[str] = set()
    for r in approved_rows:
        a = _get(r, "approver_id")
        if a and str(a) not in seen:
            seen.add(str(a))
            approvers.append(str(a))
    mean = _q2(sum(amounts, Decimal("0")) / Decimal(len(amounts)))
    return VendorBaseline(
        vendor_id=str(vendor_id) if vendor_id else None,
        vendor_name=vendor_name,
        sample_size=len(approved_rows),
        mean_amount=mean,
        median_amount=_q2(_quantile(amounts, 0.5)),
        stdev_amount=_q2(_stdev(amounts)),
        min_amount=_q2(min(amounts)),
        max_amount=_q2(max(amounts)),
        typical_approver_ids=approvers,
        median_time_to_approve_days=_quantile(times, 0.5) if times else Decimal("0"),
    )


def detect_invoice_anomaly(
    invoice,
    baseline: VendorBaseline | None,
    *,
    sigma: Decimal = Decimal("2.0"),
    median_multiple: Decimal = Decimal("3.0"),
    timing_multiple: Decimal = Decimal("3.0"),
    proposed_approver_id: str | None = None,
    time_in_review_days: Decimal | None = None,
    min_history: int = 5,
) -> InvoiceAnomaly:
    """Flag a single invoice against the per-vendor baseline.

    Read-only and explainable: the ``baseline`` it compared against is returned
    on the result. ``invoice`` is duck-typed with ``id``, ``vendor_id``,
    ``vendor_name``, ``amount``. ``min_history`` is accepted for signature
    symmetry — a ``None`` baseline already means "insufficient history".
    """
    inv_id = str(_get(invoice, "id"))
    vendor_id = _get(invoice, "vendor_id")
    vendor_id = str(vendor_id) if vendor_id else None
    vendor_name = _get(invoice, "vendor_name") or ""
    amount = Decimal(str(_get(invoice, "amount") or 0))

    if baseline is None:
        return InvoiceAnomaly(
            invoice_id=inv_id,
            vendor_id=vendor_id,
            vendor_name=vendor_name,
            amount=amount,
            baseline=None,
            flags=[],
            insufficient_history=True,
        )

    flags: list[AnomalyFlag] = []
    mean = baseline.mean_amount
    stdev = baseline.stdev_amount
    median = baseline.median_amount
    high_bound = mean + sigma * stdev
    median_bound = median * median_multiple

    # 1. amount_high — BOTH guards (σ AND median-multiple) so a tight-variance
    #    vendor doesn't trip on a small absolute jump.
    if amount > high_bound and amount > median_bound:
        if stdev > 0:
            sigmas = (amount - mean) / stdev
            message = f"Amount {amount} is {sigmas:.1f}σ above this vendor's mean of {mean}"
        else:
            message = (
                f"Amount {amount} is {(amount / median if median else Decimal('0')):.1f}× "
                f"this vendor's median of {median}"
            )
        flags.append(
            AnomalyFlag(
                code="amount_high",
                severity="warning",
                message=message,
                observed=str(amount),
                expected=str(high_bound),
            )
        )

    # 2. amount_low — unusually tiny (possible test txn / split invoice).
    if stdev > 0 and amount > 0 and amount < mean - sigma * stdev:
        low_bound = mean - sigma * stdev
        flags.append(
            AnomalyFlag(
                code="amount_low",
                severity="info",
                message=(
                    f"Amount {amount} is unusually low for this vendor "
                    f"(mean {mean}, below {low_bound})"
                ),
                observed=str(amount),
                expected=str(low_bound),
            )
        )

    # 3. unusual_approver — only when an approver is supplied.
    if (
        proposed_approver_id is not None
        and baseline.typical_approver_ids
        and proposed_approver_id not in baseline.typical_approver_ids
    ):
        flags.append(
            AnomalyFlag(
                code="unusual_approver",
                severity="info",
                message=("Proposed approver has not previously approved invoices for this vendor"),
                observed=str(proposed_approver_id),
                expected=",".join(baseline.typical_approver_ids),
            )
        )

    # 4. off_pattern_timing — in-flight invoice sitting far longer than usual.
    if (
        time_in_review_days is not None
        and baseline.median_time_to_approve_days > 0
        and time_in_review_days > baseline.median_time_to_approve_days * timing_multiple
    ):
        flags.append(
            AnomalyFlag(
                code="off_pattern_timing",
                severity="info",
                message=(
                    f"In review for {time_in_review_days} days — this vendor "
                    f"typically approves in {baseline.median_time_to_approve_days} days"
                ),
                observed=str(time_in_review_days),
                expected=str(baseline.median_time_to_approve_days * timing_multiple),
            )
        )

    return InvoiceAnomaly(
        invoice_id=inv_id,
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        amount=amount,
        baseline=baseline,
        flags=flags,
        insufficient_history=False,
    )


# ---------------------------------------------------------------------------
# Suggestion derivation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DerivedSuggestion:
    kind: str  # "auto_approve_threshold" (only kind this slice)
    vendor_id: str | None
    vendor_name: str
    title: str
    rationale: str
    payload: dict  # {"vendor_id", "suggested_threshold": str-Decimal, "based_on_n": int}
    confidence_pct: Decimal  # deterministic: consistency_pct gated by sample size
    dedupe_key: str  # stable identity for upsert: f"auto_approve_threshold:{vendor_id}"


def derive_suggestions(
    vendor_patterns: list[VendorApprovalPattern],
    *,
    min_history: int = 12,
    min_consistency_pct: Decimal = Decimal("95"),
    threshold_round_to: Decimal = Decimal("500"),
) -> list[DerivedSuggestion]:
    """Derive advisory auto-approve-threshold suggestions from vendor patterns.

    A vendor qualifies when it has ``>= min_history`` approvals, **zero**
    rejections, and **zero** modifications — every approval was unmodified
    (``unmodified_count == approved_count``). The suggested threshold is the max
    approved amount rounded **up** to ``threshold_round_to``. Advisory only —
    nothing here is applied.

    The "zero modifications" gate is **absolute**, mirroring the absolute
    ``rejected_count == 0`` gate: a percentage tolerance would let a vendor with
    real field corrections still be recommended, while the title/rationale below
    assert a spotless ``{n}/{n} approved unmodified`` record. The copy and the
    gate must agree — an admin reading "spotless" must be reading the truth.
    ``min_consistency_pct`` is retained for forward-compat/org-config symmetry;
    the absolute gate is strictly stronger, so it is applied as a floor only.
    """
    out: list[DerivedSuggestion] = []
    for vp in vendor_patterns:
        if (
            vp.approved_count >= min_history
            and vp.rejected_count == 0
            and vp.unmodified_count == vp.approved_count
            and vp.consistency_pct >= min_consistency_pct
        ):
            threshold = _q2(
                (vp.max_approved_amount / threshold_round_to).quantize(
                    Decimal("1"), rounding=ROUND_CEILING
                )
                * threshold_round_to
            )
            confidence = min(vp.consistency_pct, Decimal("99"))
            vendor_key = vp.vendor_id if vp.vendor_id else f"name:{vp.vendor_name}"
            title = (
                f"Vendor {vp.vendor_name}: {vp.unmodified_count}/{vp.approved_count} "
                f"invoices approved unmodified (median {vp.median_approved_amount}) — "
                f"consider auto-approve under ${threshold:,.0f}"
            )
            rationale = (
                f"{vp.approved_count} invoices approved with no corrections and "
                f"0 rejections, amounts {vp.min_approved_amount}–{vp.max_approved_amount} "
                f"(median {vp.median_approved_amount}). Advisory only — review before "
                f"enabling."
            )
            out.append(
                DerivedSuggestion(
                    kind="auto_approve_threshold",
                    vendor_id=vp.vendor_id,
                    vendor_name=vp.vendor_name,
                    title=title,
                    rationale=rationale,
                    payload={
                        "vendor_id": vp.vendor_id,
                        "suggested_threshold": str(threshold),
                        "based_on_n": vp.approved_count,
                    },
                    confidence_pct=confidence,
                    dedupe_key=f"auto_approve_threshold:{vendor_key}",
                )
            )
    return out
