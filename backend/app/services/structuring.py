"""Same-vendor rolling-window spend aggregation — the structuring guard.

Approval thresholds (`max_invoice_amount` / `require_cfo_above`) were only
ever evaluated per-invoice, so a payable could be split into several
under-threshold invoices for the same vendor (distinct invoice numbers, so
the exact-match duplicate detector in `invoice_warnings.py` never fires) and
each would individually clear every gate. This module sums a vendor's OTHER
recent invoices so `services.review._enforce_approval_thresholds` can
escalate on the aggregate even when no single invoice crosses it alone.

Config lives under `Organization.settings.fraud_rules` alongside the other
fraud-rule knobs (`invoice_warnings.DEFAULT_FRAUD_RULES`), not a separate
settings block.

The window is keyed off `Invoice.created_at` (server-stamped, non-null),
never `invoice_date` (nullable, and echoed straight from the vendor's own
bill or AI extraction) — a structuring vendor is exactly the party who'd
omit or backdate that field to keep a split payable off this aggregate. The
sum is also scoped to the evaluated invoice's own currency and entity, same
as `budget_service`'s rollups: unlike face values must never be added
together, and a subsidiary's spend must never bleed into a sibling's.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.services.invoice_warnings import DEFAULT_FRAUD_RULES
from app.tenant import apply_entity_scope

# Statuses that never represented real spend — excluded from the aggregate.
# Everything else counts, INCLUDING still-pending invoices: the structuring
# pattern is submitting several invoices in quick succession, not necessarily
# ones that have already cleared review.
_EXCLUDED_STATUSES = ("rejected", "failed")


def get_structuring_config(org_settings: dict | None) -> dict:
    """Merge org overrides over the structuring defaults. Mirrors
    `invoice_warnings._fraud_config`'s per-key override pattern."""
    overrides = (org_settings or {}).get("fraud_rules") or {}
    return {
        "enabled": overrides.get("structuring_enabled", DEFAULT_FRAUD_RULES["structuring_enabled"]),
        "window_days": overrides.get(
            "structuring_window_days", DEFAULT_FRAUD_RULES["structuring_window_days"]
        ),
    }


async def vendor_recent_spend(
    db: AsyncSession,
    *,
    vendor_id: uuid.UUID,
    exclude_invoice_id: uuid.UUID | None,
    window_days: int,
    currency: str = "USD",
    entity_id: uuid.UUID | None = None,
) -> Decimal:
    """Sum this vendor's OTHER invoice amounts over the trailing window.

    Excludes `exclude_invoice_id` (the invoice currently being evaluated) and
    invoices that never represented real spend (rejected/failed). Scoped to
    `currency` (never add unlike face values) and `entity_id` (never let a
    sibling subsidiary's spend inflate this one's aggregate) — both should be
    the evaluated invoice's own values.
    """
    query = select(func.coalesce(func.sum(Invoice.amount), 0)).where(
        Invoice.vendor_id == vendor_id,
        Invoice.status.notin_(_EXCLUDED_STATUSES),
        Invoice.currency == currency,
        Invoice.created_at >= datetime.now(UTC) - timedelta(days=window_days),
    )
    query = apply_entity_scope(query, Invoice, entity_id)
    if exclude_invoice_id is not None:
        query = query.where(Invoice.id != exclude_invoice_id)
    result = await db.execute(query)
    return Decimal(str(result.scalar() or 0))
