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
     holds. An **adverse-media** (negative-news) category on the
     result adds its own reason on top of whatever the verdict was —
     including on a `clear` verdict, which turns it into a hold, so
     negative news can never be auto-allowed just because the vendor
     is not on a formal list yet.

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
from app.services.payment_methods import (
    INTERNATIONAL_PAYMENT_METHODS,
    normalize_payment_method,
)
from app.services.sanctions_adapters import (
    SanctionsAdapter,
    ScreeningResult,
    get_sanctions_adapter,
)
from app.services.sanctions_categories import (
    adverse_media_reason,
    merge_categories_into_raw_response,
)

# Defaults — tenant settings override.
_DEFAULT_KYC_REQUIRED_ABOVE = Decimal("1000")
_DEFAULT_AML_ALERT_THRESHOLD = Decimal("100000")
# The corridors that require a KYC-verified vendor above the threshold. This is
# exactly "the international rails", so it is imported from the one registry
# that names them (`services/payment_methods`) rather than restated here — a
# fourth international rail must not be able to ship with no KYC gate because
# this copy was forgotten. An org can still narrow/widen it per-tenant via
# `settings.compliance.high_risk_corridor_methods`.
_DEFAULT_HIGH_RISK_METHODS: frozenset[str] = INTERNATIONAL_PAYMENT_METHODS


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


def _kyc_required_for(
    method: str,
    amount: Decimal,
    org_settings: dict | None,
    *,
    amount_currency: str | None = None,
) -> bool:
    """Does this payment need a KYC-verified vendor?

    `amount` must be denominated in the SAME currency as the configured
    `kyc_required_above` threshold — the org's home currency (the threshold is
    documented as a source-currency figure). `Payment.amount` is in the
    INVOICE's currency, so on exactly the corridors this gate governs — the
    international ones — comparing it against the threshold as bare numbers
    reads a £900 payment as under a 1000 (home-currency) threshold and skips
    the check on a ~$1,150 cross-border transfer.

    Callers therefore pass the home-currency leg (`Payment.source_amount`)
    together with its currency. When we cannot PROVE the amount is in the
    threshold's currency we **fail closed** and require KYC: an unverifiable
    comparison must not be resolved in the direction that skips a control the
    docs describe as non-overridable.
    """
    cfg = _config(org_settings)
    # Both sides are normalised: an admin typing "SEPA" into the per-org
    # override used to silently disable the KYC gate for that corridor, because
    # the stored `Payment.method` is lower-case.
    # Blank / non-string entries are dropped, so a settings blob of `[""]` falls
    # back to the default set rather than disabling the gate entirely.
    configured = {
        normalized
        for m in cfg.get("high_risk_corridor_methods") or []
        if (normalized := normalize_payment_method(m if isinstance(m, str) else None))
    }
    high_risk = configured or _DEFAULT_HIGH_RISK_METHODS
    if normalize_payment_method(method) not in high_risk:
        return False
    threshold = Decimal(str(cfg.get("kyc_required_above", _DEFAULT_KYC_REQUIRED_ABOVE)))
    home_currency = _home_currency(org_settings)
    if (amount_currency or "").strip().upper() != home_currency:
        # Not provably comparable (a foreign-currency leg, or an FX rate we
        # never locked). Require KYC rather than wave the payment through on a
        # number-vs-number comparison across two currencies.
        return True
    return amount >= threshold


def _home_currency(org_settings: dict | None) -> str:
    """The org's home currency — the denomination of every money threshold in
    `settings.compliance`. Same source `_execute_single_payment` reads when it
    decides whether a payment needs an FX leg at all, so the two can't drift.
    """
    pmt = (org_settings or {}).get("payments") or {}
    return ((pmt.get("home_currency") or "USD") or "USD").strip().upper()


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

    # `Payment.amount` is in the INVOICE's currency; `Payment.source_amount` is
    # the home-currency leg locked at submission for an international payment.
    # Summing raw `amount` across a vendor billing in several currencies makes
    # the AML total a meaningless mixture, so prefer the home-currency figure
    # wherever one was locked. The threshold this feeds is a home-currency
    # number.
    result = await db.execute(
        select(func.coalesce(func.sum(func.coalesce(Payment.source_amount, Payment.amount)), 0))
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
    # REQUIRED, deliberately with no default: the KYC threshold is denominated
    # in the org's home currency, so a caller that doesn't say what currency
    # `payment_amount` is in must fail loudly here rather than silently pick a
    # direction. Pass `Payment.source_currency` when an FX rate was locked,
    # otherwise the invoice's own currency.
    payment_currency: str | None,
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
        # The PII-free category taxonomy rides the row so `vendor_risk_scoring`
        # (compute-on-read, no adapter call) can see WHY a screen was elevated.
        raw_response=merge_categories_into_raw_response(
            screening.raw_response, screening.categories
        ),
        matched_list=screening.matched_list,
        correlation_id=correlation_id,
    )
    db.add(sanctions_row)

    if screening.result == "match":
        match_reasons = [
            f"vendor matched sanctions list "
            f"({screening.matched_list or 'unspecified'}) via {screening.provider}"
        ]
        if screening.adverse_media:
            match_reasons.append(adverse_media_reason(screening.provider))
        return ComplianceDecision(
            verdict="refuse",
            reasons=match_reasons,
            screening_result=screening,
            sanctions_check_row=sanctions_row,
        )
    if screening.result == "review_required":
        reasons.append(
            f"vendor screening returned review_required "
            f"({screening.matched_list or 'see audit row'}) via {screening.provider}"
        )
    # Adverse media is called out separately from the bare verdict — it is the
    # signal the taxonomy exists for, and "negative news" is a different
    # instruction to a reviewer than "on a watchlist". Deliberately NOT nested
    # under the `review_required` branch: a provider that reports negative news
    # alongside a `clear` verdict (nothing on a formal list yet) would otherwise
    # be auto-allowed, and one reason here is what turns the verdict into a
    # `hold` for AP review — fail closed.
    if screening.adverse_media:
        reasons.append(adverse_media_reason(screening.provider))

    # ---------- 2. KYC status on high-risk corridors -------------------------
    if _kyc_required_for(
        payment_method, payment_amount, org_settings, amount_currency=payment_currency
    ):
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
