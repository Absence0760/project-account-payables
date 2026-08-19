"""Composite vendor risk scoring.

Public symbols consumed by `api/vendor_risk.py` and tests:
`compute_vendor_risk`, `RiskAssessment`, and `recompute_and_persist`.

Design — a pure, compute-on-demand 0–100 score blended from three
PII-free signals (no external calls; reads the persisted latest
`sanctions_checks` row rather than calling an adapter, mirroring
`services/adaptive_workflows.py`'s compute-on-read style):

  * sanctions — the latest `SanctionsCheck` for the vendor. `match`
    dominates (critical); `review_required` is elevated; `clear` /
    none contributes nothing. The provider risk_score (0–100) is
    folded in when present, and an **adverse-media** (negative-news)
    category on that row raises the sub-score to at least
    `_ADVERSE_MEDIA_FLOOR` and is named in the factor breakdown — the
    taxonomy travels on the persisted row's JSONB (see
    `services/sanctions_categories`), because this service is
    compute-on-read and never calls an adapter.
  * fraud signals — count of *open* `fraud_flag` exceptions on the
    vendor's invoices.
  * payment history — trailing-12-month completed-payment volume +
    count, plus any failed / cancelled payments (a returned payment
    is a mild risk signal; high recent volume to an
    unscreened/elevated vendor is more exposure). The volume is
    expressed in the org's REPORTING currency via the shared
    `currency_conversion.payment_reporting_amount_sql` — never a raw
    `SUM(Payment.amount)`, which is denominated in each INVOICE's own
    currency and so mixes currencies the moment a vendor bills in more
    than one (see `_payment_history`).

The three are blended into a weighted composite, clamped to 0–100, and
bucketed into `low | medium | high | critical | unknown`. A sanctions
`match` (or a hard `payments_blocked` flag) forces `critical`
regardless of the numeric score.

`compute_vendor_risk` returns the breakdown without persisting;
`recompute_and_persist` writes `risk_score` / `risk_level` /
`risk_factors` / `risk_scored_at` onto the vendor row (PII-free
factors — counts / scores / list NAMES only, invariant #7) and does
NOT commit (the caller owns the transaction).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as ExceptionModel
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.sanctions_check import SanctionsCheck
from app.models.vendor import Vendor
from app.services.currency_conversion import (
    payment_reporting_amount_sql,
    resolve_reporting_currency,
)
from app.services.sanctions_categories import (
    categories_from_raw_response,
    has_adverse_media,
)

# ---------------------------------------------------------------------------
# Scoring weights. Each sub-signal yields a 0–100 sub-score; the
# composite is a weighted average (weights sum to 1.0) before the
# sanctions-match / blocked override. Documented so the breakdown is
# explainable and the weights are tunable without code archaeology.
# ---------------------------------------------------------------------------
_WEIGHT_SANCTIONS = Decimal("0.55")
_WEIGHT_FRAUD = Decimal("0.30")
_WEIGHT_PAYMENT_HISTORY = Decimal("0.15")

# Per-signal sub-score knobs.
_FRAUD_PER_FLAG = Decimal("35")  # each open fraud_flag adds this (capped at 100)
_FAILED_PAYMENT_PER = Decimal("20")  # each failed/cancelled payment in window
# Trailing-12m completed volume → exposure sub-score. Linear ramp: at
# or above this amount the volume component is maxed at 100.
_VOLUME_FULL_EXPOSURE = Decimal("100000")

# Sanctions sub-score floors. `_REVIEW_FLOOR` is what a `review_required`
# scores when the provider volunteered no risk_score of its own;
# `_ADVERSE_MEDIA_FLOOR` sits above it so a negative-news hit can never rank
# below a generic review (the mock adapter scores adverse media 50, i.e. below
# the review floor, which would otherwise invert the two).
_REVIEW_FLOOR = Decimal("60")
_ADVERSE_MEDIA_FLOOR = Decimal("65")

# Bucket thresholds on the 0–100 composite.
_HIGH_AT = Decimal("70")
_MEDIUM_AT = Decimal("40")


@dataclass
class RiskAssessment:
    """Computed risk for one vendor."""

    risk_score: Decimal | None = None
    risk_level: str = "unknown"  # low | medium | high | critical | unknown
    factors: dict = field(default_factory=dict)


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal("0"), min(Decimal("100"), value))


def _q2(value: Decimal) -> Decimal:
    """Quantise to two decimals (the `Numeric(5, 2)` column scale)."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


async def _latest_sanctions_check(db: AsyncSession, vendor_id: uuid.UUID) -> SanctionsCheck | None:
    return (
        await db.execute(
            select(SanctionsCheck)
            .where(SanctionsCheck.vendor_id == vendor_id)
            .order_by(SanctionsCheck.checked_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _open_fraud_flag_count(db: AsyncSession, vendor_id: uuid.UUID) -> int:
    """Count open `fraud_flag` exceptions on this vendor's invoices.

    Exceptions carry `invoice_id`, not `vendor_id`, so we join through
    invoices (same shape as compliance's payment→invoice→vendor join).
    """
    result = await db.execute(
        select(func.count())
        .select_from(ExceptionModel)
        .join(Invoice, Invoice.id == ExceptionModel.invoice_id)
        .where(
            Invoice.vendor_id == vendor_id,
            ExceptionModel.exception_type == "fraud_flag",
            ExceptionModel.status == "open",
        )
    )
    return int(result.scalar() or 0)


async def _payment_history(
    db: AsyncSession, vendor_id: uuid.UUID, *, reporting_currency: str
) -> tuple[Decimal, int, int, int]:
    """Trailing-12m completed volume + count, lifetime failed count, and the
    number of trailing-12m payments whose outflow can't be expressed in
    ``reporting_currency``.

    Returns ``(trailing_12m_amount, completed_count, failed_payment_count,
    unconverted_payment_count)``. Reads through the invoice→vendor join
    (Payment has no vendor_id).

    **The volume is a reporting-currency figure, not a raw SUM.**
    ``Payment.amount`` is denominated in the INVOICE's currency (see
    ``international_payments.prepare_international_payment``), so summing it
    across a vendor billing in more than one currency added e.g. ¥10,000,000
    to a USD total as though it were $10,000,000 — and that total is compared
    against ``_VOLUME_FULL_EXPOSURE``, a bare number in the org's reporting
    currency. A single foreign invoice pinned the exposure sub-score at 100 and
    could tip a vendor's bucket a whole band, on a figure the factor breakdown
    then displayed as money.

    Resolution is the SAME ``currency_conversion.payment_reporting_amount_sql``
    the 1099 report uses, so a risk score and a filed total can't disagree about
    what a payment moved. A payment neither rung can establish is left OUT of
    the volume and counted on ``unconverted_payment_count`` instead — never
    folded in at face value, and never converted at read time (a rate fetched on
    a read would make the score move under the reader).
    """
    cutoff = datetime.now(UTC) - timedelta(days=365)
    reported = payment_reporting_amount_sql(
        reporting_currency=reporting_currency,
        payment_amount=Payment.amount,
        payment_source_amount=Payment.source_amount,
        payment_source_currency=Payment.source_currency,
        invoice_currency=Invoice.currency,
    )
    countable = reported.is_expressible

    completed = await db.execute(
        select(
            # `Decimal("0")` (never int 0) so an empty window can't promote the
            # aggregate off Numeric — same guard `tax_1099.build_1099_report`
            # applies to the same helper's output.
            func.coalesce(func.sum(case((countable, reported.amount))), Decimal("0")),
            func.count(),
            func.count(case((~countable, Payment.id))),
        )
        .select_from(Payment)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(
            Invoice.vendor_id == vendor_id,
            Payment.status == "completed",
            Payment.completed_at >= cutoff,
        )
    )
    amount_sum, completed_count, unconverted_count = completed.one()

    failed = await db.execute(
        select(func.count())
        .select_from(Payment)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(
            Invoice.vendor_id == vendor_id,
            Payment.status.in_(("failed", "cancelled")),
        )
    )
    failed_count = int(failed.scalar() or 0)

    return (
        Decimal(str(amount_sum or 0)),
        int(completed_count or 0),
        failed_count,
        int(unconverted_count or 0),
    )


def _sanctions_subscore(check: SanctionsCheck | None) -> tuple[Decimal, dict]:
    """0–100 sanctions sub-score + the PII-free factor breakdown."""
    if check is None:
        return Decimal("0"), {"latest_result": None, "categories": [], "adverse_media": False}
    provider_score = Decimal(str(check.risk_score)) if check.risk_score is not None else None
    # The category taxonomy the adapter reported, folded onto the row at write
    # time (see `services/sanctions_categories`). This service is
    # compute-on-read and never calls an adapter, so the persisted row is the
    # only place the taxonomy can come from. A pre-taxonomy row reads as `()`.
    categories = categories_from_raw_response(check.raw_response)
    adverse_media = has_adverse_media(categories)
    factor = {
        "latest_result": check.result,
        "matched_list": check.matched_list,
        "score": str(provider_score) if provider_score is not None else None,
        "provider": check.provider,
        # Fixed-vocabulary labels only — safe to persist onto `risk_factors`
        # and render, unlike the provider's raw match details.
        "categories": list(categories),
        "adverse_media": adverse_media,
    }
    if check.result == "match":
        # Hard list match — max sub-score (the overall verdict is forced
        # critical anyway, but keep the numeric coherent).
        return Decimal("100"), factor

    if check.result == "review_required":
        # Elevated. Prefer the provider's own risk_score when it gave
        # one; otherwise a fixed elevated floor.
        sub = provider_score if provider_score is not None else _REVIEW_FLOOR
    else:
        # clear → no contribution from the verdict itself.
        sub = Decimal("0")

    if adverse_media:
        # Negative news outranks a bare jurisdiction flag: it is a statement
        # about this counterparty's conduct, not about where it banks. Without
        # this floor an adverse-media hit could score BELOW a generic
        # `review_required` — the mock adapter scores it 50 against the 60
        # review floor — which inverts the two signals. Applied outside the
        # `review_required` branch so a provider reporting negative news on an
        # otherwise-`clear` verdict still moves the score, matching the
        # compliance gate, which holds that payment for review.
        sub = max(sub, _ADVERSE_MEDIA_FLOOR)
    return _clamp(sub), factor


def _fraud_subscore(open_flags: int) -> tuple[Decimal, dict]:
    sub = _clamp(_FRAUD_PER_FLAG * Decimal(open_flags))
    return sub, {"open_fraud_flags": open_flags}


def _payment_subscore(
    trailing_amount: Decimal,
    completed_count: int,
    failed_count: int,
    *,
    currency: str,
    unconverted_count: int = 0,
) -> tuple[Decimal, dict]:
    # Exposure from recent volume: linear ramp to 100 at the full-exposure
    # threshold. A returned/failed payment is a mild standalone signal.
    volume_component = Decimal("0")
    if _VOLUME_FULL_EXPOSURE > 0 and trailing_amount > 0:
        volume_component = _clamp(trailing_amount / _VOLUME_FULL_EXPOSURE * Decimal("100"))
    failed_component = _clamp(_FAILED_PAYMENT_PER * Decimal(failed_count))
    sub = _clamp(max(volume_component, failed_component))
    factor = {
        # Both the figure AND the currency it is in — `_VOLUME_FULL_EXPOSURE` is
        # a bare number in this same currency, so naming it is what makes the
        # comparison legible instead of implicitly USD.
        "trailing_12m_amount": str(_q2(trailing_amount)),
        "currency": currency,
        "payment_count": completed_count,
        # Payments in the window whose outflow could not be expressed in the
        # reporting currency, so they contributed NOTHING to the volume above.
        # Surfaced rather than silently dropped: a vendor showing 0 exposure on
        # 12 unconvertible payments is a data gap to chase, not a clean record.
        "unconverted_payments": unconverted_count,
        "failed_payments": failed_count,
    }
    return sub, factor


def _bucket(score: Decimal) -> str:
    if score >= _HIGH_AT:
        return "high"
    if score >= _MEDIUM_AT:
        return "medium"
    return "low"


async def compute_vendor_risk(
    db: AsyncSession,
    *,
    vendor: Vendor,
    organization_id: uuid.UUID,
    org_settings: dict | None = None,
) -> RiskAssessment:
    """Compute (do not persist) a vendor's composite risk.

    Pure read — no adapter / network calls. Blends the latest persisted
    sanctions check, open fraud-flag count, and payment history into a
    0–100 composite + bucket + PII-free factor breakdown.
    """
    reporting_currency = resolve_reporting_currency(org_settings)
    check = await _latest_sanctions_check(db, vendor.id)
    open_flags = await _open_fraud_flag_count(db, vendor.id)
    (
        trailing_amount,
        completed_count,
        failed_count,
        unconverted_count,
    ) = await _payment_history(db, vendor.id, reporting_currency=reporting_currency)

    sanctions_sub, sanctions_factor = _sanctions_subscore(check)
    fraud_sub, fraud_factor = _fraud_subscore(open_flags)
    payment_sub, payment_factor = _payment_subscore(
        trailing_amount,
        completed_count,
        failed_count,
        currency=reporting_currency,
        unconverted_count=unconverted_count,
    )

    composite = _clamp(
        sanctions_sub * _WEIGHT_SANCTIONS
        + fraud_sub * _WEIGHT_FRAUD
        + payment_sub * _WEIGHT_PAYMENT_HISTORY
    )

    factors = {
        "sanctions": sanctions_factor,
        "fraud": fraud_factor,
        "payment_history": payment_factor,
        "weights": {
            "sanctions": str(_WEIGHT_SANCTIONS),
            "fraud": str(_WEIGHT_FRAUD),
            "payment_history": str(_WEIGHT_PAYMENT_HISTORY),
        },
        "composite": str(_q2(composite)),
    }

    # ---- Resolve the bucket ------------------------------------------------
    # A hard payment block or a sanctions list match forces `critical`,
    # whatever the numeric composite says — these are the
    # "stop-the-payment" signals the roadmap gate cares about.
    sanctions_match = check is not None and check.result == "match"
    blocked = bool(getattr(vendor, "payments_blocked", False))

    # Is there literally any signal? If nothing has ever been screened,
    # no fraud flags, and no payment history, the vendor is genuinely
    # `unknown` — don't pretend a never-touched vendor is "low".
    has_signal = (
        check is not None
        or open_flags > 0
        or completed_count > 0
        or failed_count > 0
        or trailing_amount > 0
        or blocked
    )

    if sanctions_match or blocked:
        level = "critical"
    elif not has_signal:
        level = "unknown"
    else:
        level = _bucket(composite)

    factors["override"] = {
        "sanctions_match": sanctions_match,
        "payments_blocked": blocked,
    }

    return RiskAssessment(
        risk_score=_q2(composite),
        risk_level=level,
        factors=factors,
    )


async def recompute_and_persist(
    db: AsyncSession,
    *,
    vendor: Vendor,
    organization_id: uuid.UUID,
    org_settings: dict | None = None,
) -> RiskAssessment:
    """Recompute a vendor's risk and write it onto the vendor row.

    Persists `risk_score` / `risk_level` / `risk_factors` /
    `risk_scored_at`. Does NOT commit — the caller owns the
    transaction (matches the rest of the service layer).
    """
    assessment = await compute_vendor_risk(
        db,
        vendor=vendor,
        organization_id=organization_id,
        org_settings=org_settings,
    )
    vendor.risk_score = assessment.risk_score
    vendor.risk_level = assessment.risk_level
    vendor.risk_factors = assessment.factors
    vendor.risk_scored_at = datetime.now(UTC)
    return assessment
