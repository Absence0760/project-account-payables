"""Per-vendor correction cache.

When a reviewer corrects a field during approval, the corrected value is
upserted against the vendor in `vendor_extraction_priors`. On the next
extraction for the same vendor, low-confidence values for cacheable fields
are overlaid with the stored priors.

Only "vendor-consistent" fields are cached — fields that vary per-invoice
(amount, dates, invoice_number, line_items) are never stored. See
backend/docs/ai-extraction.md § Learning from corrections.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.vendor_priors import VendorExtractionPrior
from app.services.extraction_adapters.base import ExtractionResult

logger = logging.getLogger(__name__)


# Fields whose value tends to be consistent across invoices from the same
# vendor. Extend cautiously — adding a per-invoice field here would cause
# stale cached values to clobber correct extracted ones.
CACHEABLE_FIELDS: frozenset[str] = frozenset(
    {
        "currency",
        "tax_rate",
        "payment_terms",
        "payment_method",
        "vendor_address",
        "vendor_tax_id",
        "remit_to_address",
        "gl_account",
        "cost_center",
    }
)

# Aliases applied when a reviewer submits corrections — mirrors the remap
# already done in services/review.py approve_invoice().
CORRECTION_FIELD_ALIASES: dict[str, str] = {"vendor": "vendor_name"}

# Map an Invoice/cacheable field name → the corresponding ExtractedField
# attribute on ExtractionResult, where the two differ. The AI-suggested GL
# code + cost center land on `suggested_gl_account` / `suggested_cost_center`
# (the Invoice columns are `gl_account` / `cost_center`). Without this remap,
# `getattr(result, "gl_account")` returns None → confidence reads 0.0 → the
# cached prior ALWAYS overlays, clobbering even a high-confidence AI suggestion.
_RESULT_FIELD_ALIASES: dict[str, str] = {
    "gl_account": "suggested_gl_account",
    "cost_center": "suggested_cost_center",
}

# Fields stored as Decimal on Invoice. Others are plain strings/text.
_DECIMAL_FIELDS: frozenset[str] = frozenset({"tax_rate"})

# Confidence below which an extracted field is considered a candidate for
# replacement by a cached prior. 0.8 is loose enough that genuinely-uncertain
# extractions get corrected, but tight enough that high-confidence extractions
# (which may legitimately differ from the cache — e.g. a vendor's tax rate
# changed) are left alone.
CONFIDENCE_THRESHOLD: float = 0.8


def _coerce(field: str, raw: str):
    """Coerce a stored prior back into the type expected on Invoice."""
    if field in _DECIMAL_FIELDS:
        try:
            return Decimal(raw)
        except (InvalidOperation, ValueError):
            return None
    return raw


async def record_corrections(
    db: AsyncSession,
    invoice: Invoice,
    corrections: dict,
) -> None:
    """Upsert whitelisted corrected fields against the invoice's vendor.

    Silently no-ops when the invoice isn't linked to a vendor (happens on
    brand-new vendors that haven't been matched yet) or when no corrections
    touched a cacheable field.
    """
    if not corrections or invoice.vendor_id is None:
        return

    now = datetime.now(UTC)

    for raw_key, raw_value in corrections.items():
        if raw_value is None or raw_value == "":
            continue
        field = CORRECTION_FIELD_ALIASES.get(raw_key, raw_key)
        if field not in CACHEABLE_FIELDS:
            continue

        value_str = str(raw_value)

        existing = (
            await db.execute(
                select(VendorExtractionPrior).where(
                    VendorExtractionPrior.vendor_id == invoice.vendor_id,
                    VendorExtractionPrior.field_name == field,
                )
            )
        ).scalar_one_or_none()

        if existing is None:
            db.add(
                VendorExtractionPrior(
                    vendor_id=invoice.vendor_id,
                    field_name=field,
                    value=value_str,
                    correction_count=1,
                )
            )
        else:
            existing.value = value_str
            existing.correction_count += 1
            existing.updated_at = now


async def _get_priors(db: AsyncSession, vendor_id: uuid.UUID) -> dict[str, VendorExtractionPrior]:
    rows = (
        (
            await db.execute(
                select(VendorExtractionPrior).where(VendorExtractionPrior.vendor_id == vendor_id)
            )
        )
        .scalars()
        .all()
    )
    return {r.field_name: r for r in rows}


async def apply_priors_to_invoice(
    db: AsyncSession,
    invoice: Invoice,
    result: ExtractionResult,
) -> list[str]:
    """Overlay cached priors on low-confidence extracted fields.

    Returns the list of field names that were overlaid (empty list if the
    invoice has no linked vendor, no priors exist, or every field was
    high-confidence).
    """
    if invoice.vendor_id is None:
        return []

    priors = await _get_priors(db, invoice.vendor_id)
    if not priors:
        return []

    applied: list[str] = []
    now = datetime.now(UTC)

    for field in CACHEABLE_FIELDS:
        prior = priors.get(field)
        if prior is None:
            continue

        result_attr = _RESULT_FIELD_ALIASES.get(field, field)
        extracted = getattr(result, result_attr, None)
        confidence = getattr(extracted, "confidence", 0.0) if extracted is not None else 0.0
        current_value = getattr(invoice, field, None)

        # Two conditions for overlay:
        #   1. No extracted value at all (AI found nothing) — always overlay.
        #   2. Extracted but low-confidence (below threshold) — overlay.
        should_overlay = current_value in (None, "") or confidence < CONFIDENCE_THRESHOLD
        if not should_overlay:
            continue

        coerced = _coerce(field, prior.value)
        if coerced is None:
            continue

        setattr(invoice, field, coerced)
        prior.last_applied_at = now
        applied.append(field)

    if applied:
        logger.info(
            "Applied vendor priors to invoice %s (vendor=%s): %s",
            invoice.id,
            invoice.vendor_id,
            ", ".join(applied),
        )

    return applied
