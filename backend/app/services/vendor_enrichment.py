"""Intelligent data enrichment from supplier history — pure computation.

This is the **deterministic statistics** layer for the data-enrichment slice. It
is a *sibling* to ``app.services.vendor_priors`` — NOT an extension of it. The
priors module is a correction *cache* (single row per ``(vendor, field)``,
silently overlaid onto low-confidence extractions). This module is *distribution
statistics over many historical invoices*, surfaced as **advisory hints to a
reviewer** and never written back onto the invoice. The two read overlapping
vendor history but share no write path. See ``backend/docs/data-enrichment.md``.

Three concerns, one module — every function here is sync + pure (no async, no
IO): the caller (``app.api.enrichment``) pulls the rows from the tenant DB and
hands them in already shaped, so the math is unit-testable without a database
and — critically — **deterministic** (no LLM, no cloud key; local-first
invariant). All money / price math is ``Decimal``; nothing is ever ``float``.

  1. **Auto-fill** — ``suggest_fields`` derives the dominant historical value
     for ``gl_account`` / ``cost_center`` / ``payment_terms`` with a dominance
     ratio (confidence), evidence count, and runner-up. Suggestion-only: it
     never proposes overwriting a value the draft already holds.
  2. **Price variance** — ``detect_price_variance`` builds a per-item median
     baseline from the vendor's historical line items and flags draft lines
     that deviate beyond a tolerance. Returned inline (read-only); it does NOT
     write ``Invoice.warnings`` or raise ``Exception`` rows this slice.
  3. **Vendor scoring** — ``compute_vendor_score`` combines accuracy + dispute
     (+ optional on-time) sub-scores into a renormalized composite, with clean
     N/A handling for missing data. Compute-on-read; nothing is persisted.

Amount-deviation / per-vendor amount-outlier detection is intentionally NOT
here — that already shipped in ``adaptive_workflows.detect_invoice_anomaly``.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "AUTOFILL_FIELDS",
    "FieldSuggestion",
    "PriceVarianceFlag",
    "SubScore",
    "VendorScore",
    "MIN_CONFIDENCE",
    "MIN_SAMPLE",
    "HISTORY_LIMIT",
    "PRICE_TOLERANCE_PCT",
    "PRICE_ESCALATE_PCT",
    "PRICE_MIN_HISTORY",
    "PRICE_HISTORY_LIMIT",
    "DISPUTE_EXCEPTION_TYPES",
    "suggest_fields",
    "detect_price_variance",
    "compute_vendor_score",
]

_CENTS = Decimal("0.01")
_TENTH = Decimal("0.1")
_HUNDRED = Decimal("100")

# ---------------------------------------------------------------------------
# Auto-fill — constants + dataclass
# ---------------------------------------------------------------------------

# String fields eligible for auto-fill this slice (whitelist).
AUTOFILL_FIELDS: tuple[str, ...] = ("gl_account", "cost_center", "payment_terms")

MIN_CONFIDENCE = Decimal("60.0")  # dominant value must be the majority
MIN_SAMPLE = 3  # don't suggest off one or two invoices
HISTORY_LIMIT = 50  # caller LIMIT; newest N approved invoices

# Price-variance constants.
PRICE_TOLERANCE_PCT = Decimal("15.0")
PRICE_ESCALATE_PCT = Decimal("30.0")
PRICE_MIN_HISTORY = 2  # need ≥2 prior prices to call something a baseline
PRICE_HISTORY_LIMIT = 500  # cap historical line rows pulled

# Vendor-score: exception types that represent a real, vendor-facing problem
# with the submission (used for the dispute sub-score denominator hits).
DISPUTE_EXCEPTION_TYPES: tuple[str, ...] = (
    "po_mismatch",
    "duplicate",
    "fraud_flag",
    "missing_data",
)

# Composite weights. Renormalized over only the *available* (non-N/A)
# sub-scores so an N/A on-time component doesn't penalize the vendor.
_SCORE_WEIGHTS: dict[str, Decimal] = {
    "accuracy": Decimal("0.4"),
    "dispute": Decimal("0.3"),
    "on_time": Decimal("0.3"),
}


def _q1(x: Decimal) -> Decimal:
    return x.quantize(_TENTH, rounding=ROUND_HALF_UP)


def _q2(x: Decimal) -> Decimal:
    return x.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _median(values: list[Decimal]) -> Decimal:
    """Decimal median, quantized to cents. Robust to a single outlier price.

    Empty list → ``Decimal("0.00")`` (callers guard on sample size first).
    """
    if not values:
        return Decimal("0.00")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return _q2(s[mid])
    return _q2((s[mid - 1] + s[mid]) / Decimal(2))


@dataclass(frozen=True)
class FieldSuggestion:
    field: str  # "gl_account" | "cost_center" | "payment_terms"
    value: str  # the dominant historical value
    confidence: Decimal  # dominance ratio * 100, quantized 0.1, 0..100
    sample_size: int  # invoices considered for this field (non-null only)
    occurrences: int  # how many had `value`
    evidence: str  # human string
    runner_up: str | None  # 2nd most common value, for explainability


def suggest_fields(
    history_rows: list[dict],
    current: dict,
    *,
    min_confidence: Decimal = MIN_CONFIDENCE,
    min_sample: int = MIN_SAMPLE,
) -> list[FieldSuggestion]:
    """Derive dominant-value auto-fill suggestions from vendor history.

    ``history_rows``: one dict per historical *approved-or-beyond* invoice for
    the vendor, ``{"gl_account", "cost_center", "payment_terms"}`` (values may
    be ``None``), **newest first** (the caller orders by ``created_at desc``).

    ``current``: the draft invoice's current values for the same fields. A field
    that already holds a non-empty value is never suggested (suggestion-only,
    non-destructive invariant — we never propose overwriting).

    Returns at most one suggestion per field, only when the dominant value is
    the majority (``confidence >= min_confidence``) over a large-enough sample
    (``sample_size >= min_sample``).
    """
    out: list[FieldSuggestion] = []
    for field in AUTOFILL_FIELDS:
        # Suppression: never propose overwriting a populated draft field.
        cur = current.get(field)
        if cur is not None and str(cur).strip():
            continue

        # Non-null/non-empty historical values, preserving newest-first order so
        # ties break by most-recent occurrence.
        values: list[str] = []
        for row in history_rows:
            v = row.get(field)
            if v is not None and str(v).strip():
                values.append(str(v).strip())

        sample_size = len(values)
        if sample_size < min_sample:
            continue

        # most_common() is stable in insertion order on ties; values is
        # newest-first, so the most-recently-seen value wins a tie — deterministic.
        counts = Counter(values)
        ranked = counts.most_common()
        value, occurrences = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else None

        dominance = Decimal(occurrences) / Decimal(sample_size)
        confidence = _q1(dominance * _HUNDRED)
        if confidence < min_confidence:
            continue

        out.append(
            FieldSuggestion(
                field=field,
                value=value,
                confidence=confidence,
                sample_size=sample_size,
                occurrences=occurrences,
                evidence=f"{occurrences} of {sample_size} prior invoices used {value}",
                runner_up=runner_up,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Price variance
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriceVarianceFlag:
    line_index: int  # index into the draft invoice's line_items
    item_key: str  # normalized key the baseline was built on
    description: str | None
    current_unit_price: Decimal
    baseline_unit_price: Decimal  # median of historical unit prices for the item
    delta: Decimal  # current - baseline (signed)
    delta_pct: Decimal  # (delta / baseline) * 100, quantized 0.1
    sample_size: int  # historical line count for this item
    direction: str  # "over" | "under"
    severity: str  # "warning" | "info"


_WS = re.compile(r"\s+")
_EDGE_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")


def _normalize_desc(description: str | None) -> str | None:
    """Lowercase, collapse internal whitespace, strip surrounding punctuation.

    Returns ``None`` for an empty/None description — an unkeyable line.
    """
    if description is None:
        return None
    s = _WS.sub(" ", str(description)).strip().lower()
    s = _EDGE_PUNCT.sub("", s).strip()
    return s or None


def _item_key(item_code: str | None, description: str | None) -> str | None:
    """Deterministic item key. Prefer ``item_code``, fall back to normalized
    description. ``None`` when neither yields a usable key (unkeyable line)."""
    if item_code is not None and str(item_code).strip():
        return f"code:{str(item_code).strip().lower()}"
    norm = _normalize_desc(description)
    if norm is not None:
        return f"desc:{norm}"
    return None


def _currency_of(row: dict) -> str:
    """Normalize a row's ``currency`` to an upper-case code, defaulting to the
    same fallback the ``Invoice.currency`` column uses (``USD``). Keeps the
    baseline keyed consistently whether or not the caller threaded currency."""
    cur = row.get("currency")
    if cur is None or not str(cur).strip():
        return "USD"
    return str(cur).strip().upper()


def detect_price_variance(
    draft_lines: list[dict],
    history_lines: list[dict],
    *,
    tolerance_pct: Decimal = PRICE_TOLERANCE_PCT,
    escalate_pct: Decimal = PRICE_ESCALATE_PCT,
    min_history: int = PRICE_MIN_HISTORY,
) -> list[PriceVarianceFlag]:
    """Flag draft line items whose unit price deviates from the vendor's
    per-item historical median by more than ``tolerance_pct``.

    ``draft_lines``: ``[{"item_code", "description", "currency", "unit_price"}]``
    in line order (index becomes ``line_index``). ``unit_price`` is
    ``Decimal|None``; ``currency`` an ISO code (defaults to ``USD``).
    ``history_lines``: same shape, from the vendor's approved-or-beyond invoice
    line items (``unit_price is None`` rows are dropped).

    The baseline is keyed by ``(item_key, currency)`` so a draft line is only
    ever compared against same-currency history — a vendor that bills in both
    USD and EUR will not have its USD line judged against an EUR median (that
    pooled comparison produced a bogus ``delta_pct`` and a false over/under
    flag). A line with no same-currency history is skipped (N/A), exactly like
    a line with too little history.

    Median baseline (not mean) — robust to a single outlier historical price.
    All math ``Decimal``; div-by-zero baselines are skipped.
    """
    # Build per-(item, currency) baseline price lists from history.
    baseline: dict[tuple[str, str], list[Decimal]] = {}
    for row in history_lines:
        price = row.get("unit_price")
        if price is None:
            continue
        item = _item_key(row.get("item_code"), row.get("description"))
        if item is None:
            continue
        baseline.setdefault((item, _currency_of(row)), []).append(Decimal(str(price)))

    flags: list[PriceVarianceFlag] = []
    for idx, line in enumerate(draft_lines):
        price = line.get("unit_price")
        if price is None:
            continue
        item = _item_key(line.get("item_code"), line.get("description"))
        if item is None:
            continue
        key = (item, _currency_of(line))
        hist = baseline.get(key)
        if hist is None or len(hist) < min_history:
            continue
        baseline_price = _median(hist)
        if baseline_price == 0:
            continue

        current = Decimal(str(price))
        delta = current - baseline_price
        delta_pct = _q1(delta / baseline_price * _HUNDRED)
        if abs(delta_pct) < tolerance_pct:
            continue

        flags.append(
            PriceVarianceFlag(
                line_index=idx,
                item_key=item,
                description=line.get("description"),
                current_unit_price=_q2(current),
                baseline_unit_price=baseline_price,
                delta=_q2(delta),
                delta_pct=delta_pct,
                sample_size=len(hist),
                direction="over" if delta > 0 else "under",
                severity="warning" if abs(delta_pct) >= escalate_pct else "info",
            )
        )
    return flags


# ---------------------------------------------------------------------------
# Vendor performance scoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubScore:
    name: str  # "on_time" | "accuracy" | "dispute"
    score: Decimal | None  # 0..100, quantized 0.1; None == N/A (excluded)
    sample_size: int
    detail: str  # explainable one-liner


@dataclass(frozen=True)
class VendorScore:
    vendor_id: str
    vendor_name: str
    composite: Decimal | None  # weighted mean of available sub-scores; None if none
    sub_scores: list[SubScore]
    computed_at: str  # ISO; set by the API layer


def _accuracy_subscore(approved_count: int, corrected_count: int) -> SubScore:
    """``accuracy = (1 - correction_rate) * 100`` over approved-or-beyond
    invoices, where ``correction_rate`` is the fraction whose approval carried
    field corrections (``invoice.approved`` audit row with non-empty
    ``details.changes``)."""
    if approved_count <= 0:
        return SubScore(
            name="accuracy", score=None, sample_size=0, detail="No approved invoices yet"
        )
    rate = Decimal(corrected_count) / Decimal(approved_count)
    score = _q1((Decimal(1) - rate) * _HUNDRED)
    clean = approved_count - corrected_count
    return SubScore(
        name="accuracy",
        score=score,
        sample_size=approved_count,
        detail=f"{clean} of {approved_count} approved invoices needed no corrections",
    )


def _dispute_subscore(total_invoices: int, exception_invoices: int) -> SubScore:
    """``dispute = (1 - exception_rate) * 100`` where ``exception_rate`` is the
    fraction of the vendor's invoices (any status) that raised a vendor-facing
    exception (status-agnostic — friction that *happened* counts)."""
    if total_invoices <= 0:
        return SubScore(name="dispute", score=None, sample_size=0, detail="No invoices yet")
    rate = Decimal(exception_invoices) / Decimal(total_invoices)
    score = _q1((Decimal(1) - rate) * _HUNDRED)
    return SubScore(
        name="dispute",
        score=score,
        sample_size=total_invoices,
        detail=f"{exception_invoices} of {total_invoices} invoices raised an exception",
    )


def _ontime_subscore(ontime_input: dict | None) -> SubScore:
    """On-time delivery. N/A by default this slice — there is no PO-side
    expected/promised date to compare a goods-receipt ``received_date`` against.

    Only computed when the caller passes the opt-in ``due_date`` proxy result
    (``{"gr_count", "on_time_count"}``); otherwise N/A. The proxy
    (received_date <= invoice.due_date) is a weak approximation and is gated
    behind the ``ontime_use_due_date_proxy`` org flag, default off.
    """
    if not ontime_input:
        return SubScore(
            name="on_time",
            score=None,
            sample_size=0,
            detail="On-time delivery requires PO expected dates, not tracked yet",
        )
    gr_count = int(ontime_input.get("gr_count", 0))
    on_time_count = int(ontime_input.get("on_time_count", 0))
    if gr_count <= 0:
        return SubScore(
            name="on_time",
            score=None,
            sample_size=0,
            detail="No goods receipts with a comparable invoice due date",
        )
    score = _q1(Decimal(on_time_count) / Decimal(gr_count) * _HUNDRED)
    return SubScore(
        name="on_time",
        score=score,
        sample_size=gr_count,
        detail=(
            f"{on_time_count} of {gr_count} receipts on or before the invoice due "
            f"date (due-date proxy)"
        ),
    )


def compute_vendor_score(
    *,
    vendor_id: str,
    vendor_name: str,
    accuracy_input: dict,
    dispute_input: dict,
    ontime_input: dict | None = None,
) -> VendorScore:
    """Combine the three sub-scores into a renormalized composite.

    Inputs are already-aggregated primitives so this is DB-free / testable:
      * ``accuracy_input``: ``{"approved_count", "corrected_count"}``
      * ``dispute_input``: ``{"total_invoices", "exception_invoices"}``
      * ``ontime_input``: ``{"gr_count", "on_time_count"}`` or ``None`` (N/A)

    The composite is the weight-renormalized mean over only the available
    (non-N/A) sub-scores, so an N/A component drops out cleanly rather than
    dragging the score toward zero. ``None`` composite when none are available.
    """
    sub_scores = [
        _accuracy_subscore(
            int(accuracy_input.get("approved_count", 0)),
            int(accuracy_input.get("corrected_count", 0)),
        ),
        _dispute_subscore(
            int(dispute_input.get("total_invoices", 0)),
            int(dispute_input.get("exception_invoices", 0)),
        ),
        _ontime_subscore(ontime_input),
    ]

    weighted_sum = Decimal(0)
    weight_total = Decimal(0)
    for s in sub_scores:
        if s.score is None:
            continue
        w = _SCORE_WEIGHTS[s.name]
        weighted_sum += w * s.score
        weight_total += w

    composite = _q1(weighted_sum / weight_total) if weight_total > 0 else None

    return VendorScore(
        vendor_id=vendor_id,
        vendor_name=vendor_name,
        composite=composite,
        sub_scores=sub_scores,
        computed_at="",  # set by the API layer
    )
