"""Intelligent data enrichment from supplier history — async SQL + shaping.

Two read-only, advisory surfaces over the tenant's *own* historical invoice
data (deterministic statistics, no external calls, no cloud key — local-first):

  * ``GET /api/enrichment/invoices/{id}/suggestions`` — auto-fill field
    suggestions (GL / cost-center / terms) + inline line-item price-variance
    flags for a draft invoice. Suggestion-only: it never mutates the invoice.
  * ``GET /api/enrichment/vendors/{id}/score`` — a vendor performance score
    (accuracy + dispute, on-time N/A by default). Compute-on-read; nothing is
    persisted.

The pure math lives in ``app.services.vendor_enrichment``; this file does the
tenant-scoped SQL, the entity scoping, and the string-Decimal response shaping.
No vendor PII (``tax_id`` / ``bank_details`` / address) ever enters a response
or a log line — only vendor id + name. See ``backend/docs/data-enrichment.md``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    require_roles,
)
from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.organization import Organization
from app.models.procurement import GoodsReceipt, PurchaseOrder
from app.models.user import User
from app.models.vendor import Vendor
from app.models.workflow import AuditLog
from app.schemas.enrichment import (
    EnrichmentSuggestionsResponse,
    VendorScoreResponse,
)
from app.services.vendor_enrichment import (
    DISPUTE_EXCEPTION_TYPES,
    HISTORY_LIMIT,
    PRICE_HISTORY_LIMIT,
    compute_vendor_score,
    detect_price_variance,
    suggest_fields,
)
from app.tenant import apply_entity_scope, get_entity_id, get_tenant, get_tenant_db

router = APIRouter(prefix="/enrichment", tags=["enrichment"])

# Clerks review drafts, so the suggestions endpoint admits them. The vendor
# score is a managerial view and excludes the clerk.
_SUGGEST_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)
_SCORE_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)

# Approved-or-beyond — a human-accepted coding/price baseline. Draft / rejected
# invoices are unreviewed noise and excluded. Mirrors adaptive_workflows.
_APPROVED_STATUSES = (
    InvoiceStatus.approved,
    InvoiceStatus.sending_to_erp,
    InvoiceStatus.sent_to_erp,
    InvoiceStatus.posted_in_erp,
    InvoiceStatus.payment_scheduled,
    InvoiceStatus.paid,
    InvoiceStatus.done,
)


def _enrichment_settings(org: Organization) -> dict:
    """Merge org overrides (``settings.enrichment``) over the defaults.

    Unknown keys are dropped and numeric coercion is guarded — mirrors the
    ``_adaptive_settings`` pattern. All defaults are safe / local-first (no key
    required); the proxy is off by default.
    """
    defaults = {
        "autofill_min_confidence": Decimal("60.0"),
        "autofill_min_sample": 3,
        "price_tolerance_pct": Decimal("15.0"),
        "price_escalate_pct": Decimal("30.0"),
        "price_min_history": 2,
        "ontime_use_due_date_proxy": False,
    }
    overrides = (org.settings or {}).get("enrichment") or {}
    merged = dict(defaults)
    int_keys = {"autofill_min_sample", "price_min_history"}
    for k, v in overrides.items():
        if k not in merged:
            continue
        if k == "ontime_use_due_date_proxy":
            merged[k] = bool(v)
        elif k in int_keys:
            try:
                merged[k] = int(v)
            except (TypeError, ValueError):
                pass
        else:
            try:
                merged[k] = Decimal(str(v))
            except (TypeError, ValueError):
                pass
    return merged


# ---------------------------------------------------------------------------
# GET /api/enrichment/invoices/{invoice_id}/suggestions
# ---------------------------------------------------------------------------


@router.get(
    "/invoices/{invoice_id}/suggestions",
    response_model=EnrichmentSuggestionsResponse,
)
async def invoice_suggestions(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_SUGGEST_ROLES)),
):
    inv = (
        await db.execute(
            apply_entity_scope(select(Invoice).where(Invoice.id == invoice_id), Invoice, entity_id)
        )
    ).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")

    cfg = _enrichment_settings(org)
    field_suggestions: list = []
    price_variances: list = []

    # A vendor-less draft has no history we can safely attribute (we do NOT
    # name-match for auto-fill — too loose; it could suggest another vendor's
    # GL). Return empty arrays gracefully.
    if inv.vendor_id is not None:
        # --- Auto-fill history (approved-or-beyond, newest first, bounded) ---
        hist_q = apply_entity_scope(
            select(Invoice.gl_account, Invoice.cost_center, Invoice.payment_terms)
            .where(
                Invoice.vendor_id == inv.vendor_id,
                Invoice.id != inv.id,
                Invoice.status.in_(_APPROVED_STATUSES),
            )
            .order_by(Invoice.created_at.desc())
            .limit(HISTORY_LIMIT),
            Invoice,
            entity_id,
        )
        history_rows = [dict(r._mapping) for r in (await db.execute(hist_q)).all()]
        current = {
            "gl_account": inv.gl_account,
            "cost_center": inv.cost_center,
            "payment_terms": inv.payment_terms,
        }
        field_suggestions = suggest_fields(
            history_rows,
            current,
            min_confidence=cfg["autofill_min_confidence"],
            min_sample=cfg["autofill_min_sample"],
        )

        # --- Price-variance history (this vendor's approved line items) ---
        # Pull each line's invoice currency so the baseline is keyed per
        # currency — a vendor billing in multiple currencies must not have a
        # USD line compared against an EUR median (that pooled comparison
        # yields a bogus delta_pct + a false over/under flag).
        line_q = apply_entity_scope(
            select(
                InvoiceLineItem.item_code,
                InvoiceLineItem.description,
                InvoiceLineItem.unit_price,
                Invoice.currency,
            )
            .join(Invoice, Invoice.id == InvoiceLineItem.invoice_id)
            .where(
                Invoice.vendor_id == inv.vendor_id,
                Invoice.id != inv.id,
                Invoice.status.in_(_APPROVED_STATUSES),
                InvoiceLineItem.unit_price.is_not(None),
            )
            .order_by(Invoice.created_at.desc())
            .limit(PRICE_HISTORY_LIMIT),
            Invoice,
            entity_id,
        )
        history_lines = [dict(r._mapping) for r in (await db.execute(line_q)).all()]

        # The draft's own currency tags every draft line so it's compared only
        # against same-currency history.
        draft_lines_q = (
            select(
                InvoiceLineItem.item_code,
                InvoiceLineItem.description,
                InvoiceLineItem.unit_price,
            )
            .where(InvoiceLineItem.invoice_id == inv.id)
            .order_by(InvoiceLineItem.line_number.asc().nulls_last(), InvoiceLineItem.id.asc())
        )
        draft_lines = [
            {**dict(r._mapping), "currency": inv.currency}
            for r in (await db.execute(draft_lines_q)).all()
        ]

        price_variances = detect_price_variance(
            draft_lines,
            history_lines,
            tolerance_pct=cfg["price_tolerance_pct"],
            escalate_pct=cfg["price_escalate_pct"],
            min_history=cfg["price_min_history"],
        )

    return EnrichmentSuggestionsResponse(
        invoice_id=str(inv.id),
        vendor_id=str(inv.vendor_id) if inv.vendor_id else None,
        field_suggestions=[
            {
                "field": f.field,
                "value": f.value,
                "confidence": str(f.confidence),
                "sample_size": f.sample_size,
                "occurrences": f.occurrences,
                "evidence": f.evidence,
                "runner_up": f.runner_up,
            }
            for f in field_suggestions
        ],
        price_variances=[
            {
                "line_index": p.line_index,
                "item_key": p.item_key,
                "description": p.description,
                "current_unit_price": str(p.current_unit_price),
                "baseline_unit_price": str(p.baseline_unit_price),
                "delta": str(p.delta),
                "delta_pct": str(p.delta_pct),
                "sample_size": p.sample_size,
                "direction": p.direction,
                "severity": p.severity,
            }
            for p in price_variances
        ],
        generated_at=datetime.now(UTC).isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /api/enrichment/vendors/{vendor_id}/score
# ---------------------------------------------------------------------------


async def _accuracy_input(
    db: AsyncSession, *, vendor_id: uuid.UUID, entity_id: uuid.UUID | None
) -> dict:
    """Count this vendor's approved-or-beyond invoices that carry an
    ``invoice.approved`` audit row, and how many of those approvals included
    field corrections (``details.changes`` non-empty). Mirrors the
    ``adaptive_workflows`` correction signal."""
    base = (
        select(Invoice.id, AuditLog.details)
        .join(
            AuditLog,
            (AuditLog.entity_id == Invoice.id)
            & (AuditLog.entity_type == "invoice")
            & (AuditLog.action == "invoice.approved"),
        )
        .where(
            Invoice.vendor_id == vendor_id,
            Invoice.status.in_(_APPROVED_STATUSES),
        )
    )
    base = apply_entity_scope(base, Invoice, entity_id)
    rows = (await db.execute(base)).all()
    # An invoice can carry MORE THAN ONE `invoice.approved` audit row — a
    # rejected → re-approved cycle, or a voided payment returning the invoice to
    # `approved` and being re-approved, each writes another. Counting raw join
    # rows would double-count those invoices in both the denominator
    # (sample_size) and, if an approval carried `details.changes`, the
    # numerator. Collapse to one record per invoice: an invoice counts as
    # "corrected" if ANY of its approvals carried field changes.
    corrected_ids: set = set()
    approved_ids: set = set()
    for inv_id, details in rows:
        approved_ids.add(inv_id)
        if (details or {}).get("changes"):
            corrected_ids.add(inv_id)
    return {"approved_count": len(approved_ids), "corrected_count": len(corrected_ids)}


async def _dispute_input(
    db: AsyncSession, *, vendor_id: uuid.UUID, entity_id: uuid.UUID | None
) -> dict:
    """Total invoices for the vendor (any status) and the count of distinct
    invoices that raised a vendor-facing exception."""
    total_q = apply_entity_scope(
        select(func.count(Invoice.id)).where(Invoice.vendor_id == vendor_id),
        Invoice,
        entity_id,
    )
    total_invoices = (await db.execute(total_q)).scalar_one()

    exc_q = (
        select(func.count(distinct(APException.invoice_id)))
        .select_from(APException)
        .join(Invoice, Invoice.id == APException.invoice_id)
        .where(
            Invoice.vendor_id == vendor_id,
            APException.exception_type.in_(DISPUTE_EXCEPTION_TYPES),
        )
    )
    exc_q = apply_entity_scope(exc_q, Invoice, entity_id)
    exception_invoices = (await db.execute(exc_q)).scalar_one()
    return {
        "total_invoices": int(total_invoices or 0),
        "exception_invoices": int(exception_invoices or 0),
    }


async def _ontime_input(
    db: AsyncSession, *, vendor_id: uuid.UUID, entity_id: uuid.UUID | None
) -> dict:
    """Opt-in due-date proxy for on-time delivery (gated; only called when the
    org flag is on). Joins GR → PO (vendor) → Invoice (po_number) and compares
    ``received_date <= invoice.due_date``. A weak proxy — invoice due date is
    not the delivery-promised date — hence opt-in only."""
    q = (
        select(GoodsReceipt.received_date, Invoice.due_date)
        .join(PurchaseOrder, PurchaseOrder.id == GoodsReceipt.po_id)
        .join(Invoice, Invoice.po_number == PurchaseOrder.po_number)
        .where(
            PurchaseOrder.vendor_id == vendor_id,
            GoodsReceipt.received_date.is_not(None),
            Invoice.due_date.is_not(None),
        )
    )
    q = apply_entity_scope(q, Invoice, entity_id)
    rows = (await db.execute(q)).all()
    gr_count = len(rows)
    on_time_count = sum(1 for received, due in rows if received <= due)
    return {"gr_count": gr_count, "on_time_count": on_time_count}


@router.get("/vendors/{vendor_id}/score", response_model=VendorScoreResponse)
async def vendor_score(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_SCORE_ROLES)),
):
    vendor = (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one_or_none()
    if vendor is None:
        raise HTTPException(status_code=404, detail="Vendor not found")

    cfg = _enrichment_settings(org)
    accuracy_input = await _accuracy_input(db, vendor_id=vendor_id, entity_id=entity_id)
    dispute_input = await _dispute_input(db, vendor_id=vendor_id, entity_id=entity_id)
    ontime_input = None
    if cfg["ontime_use_due_date_proxy"]:
        ontime_input = await _ontime_input(db, vendor_id=vendor_id, entity_id=entity_id)

    score = compute_vendor_score(
        vendor_id=str(vendor.id),
        vendor_name=vendor.name,
        accuracy_input=accuracy_input,
        dispute_input=dispute_input,
        ontime_input=ontime_input,
    )

    return VendorScoreResponse(
        vendor_id=score.vendor_id,
        vendor_name=score.vendor_name,
        composite=None if score.composite is None else str(score.composite),
        sub_scores=[
            {
                "name": s.name,
                "score": None if s.score is None else str(s.score),
                "sample_size": s.sample_size,
                "detail": s.detail,
            }
            for s in score.sub_scores
        ],
        computed_at=datetime.now(UTC).isoformat(),
    )
