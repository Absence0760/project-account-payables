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
    folded in when present.
  * fraud signals — count of *open* `fraud_flag` exceptions on the
    vendor's invoices.
  * payment history — trailing-12-month completed-payment volume +
    count, plus any failed / cancelled payments (a returned payment
    is a mild risk signal; high recent volume to an
    unscreened/elevated vendor is more exposure).

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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as ExceptionModel
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.sanctions_check import SanctionsCheck
from app.models.vendor import Vendor

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


async def _payment_history(db: AsyncSession, vendor_id: uuid.UUID) -> tuple[Decimal, int, int]:
    """Trailing-12m completed volume + count, and lifetime failed count.

    Returns `(trailing_12m_amount, completed_count, failed_payment_count)`.
    Reads through the invoice→vendor join (Payment has no vendor_id).
    """
    cutoff = datetime.now(UTC) - timedelta(days=365)

    completed = await db.execute(
        select(
            func.coalesce(func.sum(Payment.amount), 0),
            func.count(),
        )
        .select_from(Payment)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(
            Invoice.vendor_id == vendor_id,
            Payment.status == "completed",
            Payment.completed_at >= cutoff,
        )
    )
    amount_sum, completed_count = completed.one()

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

    return Decimal(str(amount_sum or 0)), int(completed_count or 0), failed_count


def _sanctions_subscore(check: SanctionsCheck | None) -> tuple[Decimal, dict]:
    """0–100 sanctions sub-score + the PII-free factor breakdown."""
    if check is None:
        return Decimal("0"), {"latest_result": None}
    provider_score = Decimal(str(check.risk_score)) if check.risk_score is not None else None
    factor = {
        "latest_result": check.result,
        "matched_list": check.matched_list,
        "score": str(provider_score) if provider_score is not None else None,
        "provider": check.provider,
    }
    if check.result == "match":
        # Hard list match — max sub-score (the overall verdict is forced
        # critical anyway, but keep the numeric coherent).
        return Decimal("100"), factor
    if check.result == "review_required":
        # Elevated. Prefer the provider's own risk_score when it gave
        # one; otherwise a fixed elevated floor.
        return (provider_score if provider_score is not None else Decimal("60")), factor
    # clear → no contribution.
    return Decimal("0"), factor


def _fraud_subscore(open_flags: int) -> tuple[Decimal, dict]:
    sub = _clamp(_FRAUD_PER_FLAG * Decimal(open_flags))
    return sub, {"open_fraud_flags": open_flags}


def _payment_subscore(
    trailing_amount: Decimal, completed_count: int, failed_count: int
) -> tuple[Decimal, dict]:
    # Exposure from recent volume: linear ramp to 100 at the full-exposure
    # threshold. A returned/failed payment is a mild standalone signal.
    volume_component = Decimal("0")
    if _VOLUME_FULL_EXPOSURE > 0 and trailing_amount > 0:
        volume_component = _clamp(trailing_amount / _VOLUME_FULL_EXPOSURE * Decimal("100"))
    failed_component = _clamp(_FAILED_PAYMENT_PER * Decimal(failed_count))
    sub = _clamp(max(volume_component, failed_component))
    factor = {
        "trailing_12m_amount": str(_q2(trailing_amount)),
        "payment_count": completed_count,
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
    check = await _latest_sanctions_check(db, vendor.id)
    open_flags = await _open_fraud_flag_count(db, vendor.id)
    trailing_amount, completed_count, failed_count = await _payment_history(db, vendor.id)

    sanctions_sub, sanctions_factor = _sanctions_subscore(check)
    fraud_sub, fraud_factor = _fraud_subscore(open_flags)
    payment_sub, payment_factor = _payment_subscore(trailing_amount, completed_count, failed_count)

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
