"""KYC / AML compliance orchestration.

The international-payments orchestrator calls
`check_payment_compliance` before submitting any cross-border or
cross-currency payment. The return value is a `ComplianceDecision`:

  - allow — payment proceeds
  - hold — payment stays in `pending_compliance`; an exception is
    opened for AP review (one or more reasons listed)
  - refuse — payment is failed outright (sanctions match)

Three sub-checks, run in order so the most-severe verdict wins:

  1. Sanctions / PEP screening via the configured
     `sanctions_adapter`. A `match` refuses; a `review_required`
     holds.

  2. KYC status on the vendor row. Corridors with
     `requires_kyc=True` refuse if `vendor.kyc_status != "verified"`.
     Today's mapping: every international corridor (SEPA / SWIFT
     wire / international_ach) for amounts above the org's
     configurable threshold (default $1k) requires KYC. Domestic
     ACH does not.

  3. Per-vendor cumulative spend monitoring (basic AML signal). If
     the running total of completed payments to this vendor in the
     trailing 12 months exceeds the org's configurable
     `aml_spend_alert_threshold` (default $100k), open a review
     exception (does NOT refuse — too many false positives).

Each sub-check writes a `sanctions_checks` row (for the screening
call) and / or annotates the `ComplianceDecision.reasons` list. The
caller persists the decision on the Payment row and writes the
exception (if any).

Threshold knobs live in `Organization.settings.compliance`:

    {
      "sanctions":               { "provider": "...", "api_key": "..." },
      "kyc_required_above":      "1000",     # source-currency amount
      "aml_spend_alert_threshold": "100000",
      "high_risk_corridor_methods": ["sepa", "international_wire", ...]
    }
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment
from app.models.sanctions_check import SanctionsCheck
from app.models.vendor import Vendor
from app.services.sanctions_adapters import (
    SanctionsAdapter,
    ScreeningResult,
    get_sanctions_adapter,
)

# Defaults — tenant settings override.
_DEFAULT_KYC_REQUIRED_ABOVE = Decimal("1000")
_DEFAULT_AML_ALERT_THRESHOLD = Decimal("100000")
_DEFAULT_HIGH_RISK_METHODS: frozenset[str] = frozenset(
    {"sepa", "international_wire", "international_ach"}
)


@dataclass
class ComplianceDecision:
    """Combined verdict of every sub-check.

    `verdict` is the resolved overall outcome:
      - "allow"    → proceed with payment
      - "hold"     → keep in pending_compliance; open review exception
      - "refuse"   → fail the payment with reasons in `failure_reason`

    `reasons` is a human-readable list (each entry one short sentence)
    suitable for surfacing into the exception body. We DO NOT include
    raw provider-side strings here — sanctions providers can echo PII
    in match details, and the orchestrator never lets that bleed into
    a downstream sink (invariant #7).

    `screening_result` is the underlying sanctions adapter result.
    `sanctions_check_row` is the persisted audit row (set by the
    caller after the DB write).
    """

    verdict: str
    reasons: list[str] = field(default_factory=list)
    screening_result: ScreeningResult | None = None
    sanctions_check_row: SanctionsCheck | None = None


def _config(org_settings: dict | None) -> dict:
    return (org_settings or {}).get("compliance") or {}


def _kyc_required_for(method: str, amount: Decimal, org_settings: dict | None) -> bool:
    cfg = _config(org_settings)
    high_risk = set(cfg.get("high_risk_corridor_methods", []) or []) or _DEFAULT_HIGH_RISK_METHODS
    if method not in high_risk:
        return False
    threshold = Decimal(str(cfg.get("kyc_required_above", _DEFAULT_KYC_REQUIRED_ABOVE)))
    return amount >= threshold


def _aml_threshold(org_settings: dict | None) -> Decimal:
    cfg = _config(org_settings)
    return Decimal(str(cfg.get("aml_spend_alert_threshold", _DEFAULT_AML_ALERT_THRESHOLD)))


def _sanctions_adapter_from_settings(org_settings: dict | None) -> SanctionsAdapter:
    cfg = _config(org_settings).get("sanctions") or {}
    return get_sanctions_adapter(cfg)


async def _trailing_12m_spend(
    db: AsyncSession,
    vendor_id: uuid.UUID,
) -> Decimal:
    """Sum of completed payments to this vendor in the last 365 days.

    Reads through the invoice→vendor join because Payment doesn't
    carry vendor_id directly. We bound the lookback to a year to
    keep the query cheap; the AML signal is "unusual recent uptick",
    not lifetime spend.
    """
    cutoff = datetime.now(UTC) - timedelta(days=365)
    from app.models.invoice import Invoice

    result = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .select_from(Payment)
        .join(Invoice, Invoice.id == Payment.invoice_id)
        .where(
            Invoice.vendor_id == vendor_id,
            Payment.status == "completed",
            Payment.completed_at >= cutoff,
        )
    )
    return Decimal(str(result.scalar() or 0))


async def check_payment_compliance(
    db: AsyncSession,
    *,
    vendor: Vendor,
    payment_amount: Decimal,
    payment_method: str,
    org_settings: dict | None,
    organization_id: uuid.UUID,
    correlation_id: uuid.UUID | None = None,
    sanctions_adapter: SanctionsAdapter | None = None,
) -> ComplianceDecision:
    """Run the full sub-check chain. Caller persists the decision.

    `sanctions_adapter` is injectable so tests don't need to monkey-
    patch the dispatcher. Production callers leave it None and the
    adapter is resolved from org_settings.
    """
    # ---------- 0. Hard payment block (sticky) -------------------------------
    # A vendor flagged with `payments_blocked` (by a sanctions match in a prior
    # screen, or a manual AP block) is refused before any adapter call or FX
    # lock — the roadmap "flag and block payments to sanctioned entities" gate.
    # Unlike the per-payment screen below, the block is sticky across future
    # payments until an AP user explicitly unblocks the vendor.
    if getattr(vendor, "payments_blocked", False):
        reason = getattr(vendor, "payments_blocked_reason", None) or "sanctions/compliance block"
        return ComplianceDecision(
            verdict="refuse",
            reasons=[f"vendor is blocked from payment: {reason}"],
        )

    reasons: list[str] = []
    adapter = sanctions_adapter or _sanctions_adapter_from_settings(org_settings)

    # ---------- 1. Sanctions / PEP screening ---------------------------------
    bo_blob = vendor.beneficial_owner_data or {}
    beneficial_owners = bo_blob.get("owners") if isinstance(bo_blob, dict) else None

    screening = await adapter.screen_vendor(
        vendor_name=vendor.name,
        vendor_country=(vendor.bank_details or {}).get("country") if vendor.bank_details else None,
        vendor_tax_id=vendor.tax_id,
        beneficial_owners=beneficial_owners,
    )

    sanctions_row = SanctionsCheck(
        vendor_id=vendor.id,
        organization_id=organization_id,
        provider=screening.provider,
        check_type="pre_payment",
        result=screening.result,
        risk_score=screening.risk_score,
        matched_list=screening.matched_list,
        raw_response=screening.raw_response,
        correlation_id=correlation_id,
    )
    db.add(sanctions_row)

    if screening.result == "match":
        return ComplianceDecision(
            verdict="refuse",
            reasons=[
                f"vendor matched sanctions list "
                f"({screening.matched_list or 'unspecified'}) via {screening.provider}"
            ],
            screening_result=screening,
            sanctions_check_row=sanctions_row,
        )
    if screening.result == "review_required":
        reasons.append(
            f"vendor screening returned review_required "
            f"({screening.matched_list or 'see audit row'}) via {screening.provider}"
        )

    # ---------- 2. KYC status on high-risk corridors -------------------------
    if _kyc_required_for(payment_method, payment_amount, org_settings):
        if vendor.kyc_status != "verified":
            reasons.append(
                f"corridor '{payment_method}' requires KYC; vendor.kyc_status='{vendor.kyc_status}'"
            )
            # KYC gap on a high-risk corridor is a refusal, not a hold —
            # we can't proceed until the vendor is verified. A hold
            # would imply an AP team can override, which contradicts
            # the regulatory intent.
            return ComplianceDecision(
                verdict="refuse",
                reasons=reasons,
                screening_result=screening,
                sanctions_check_row=sanctions_row,
            )

    # ---------- 3. AML trailing-12m spend signal -----------------------------
    threshold = _aml_threshold(org_settings)
    if threshold > 0:
        trailing = await _trailing_12m_spend(db, vendor.id)
        projected = trailing + payment_amount
        if projected >= threshold:
            reasons.append(
                f"trailing 12-month spend with vendor would reach "
                f"${projected} (threshold ${threshold}); AP review required"
            )

    verdict = "hold" if reasons else "allow"
    return ComplianceDecision(
        verdict=verdict,
        reasons=reasons,
        screening_result=screening,
        sanctions_check_row=sanctions_row,
    )
