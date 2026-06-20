"""Single chokepoint for creating an AP ``Exception`` row + emitting ``exception.raised``.

Every code path that opens an exception (duplicate / fraud / po_mismatch /
price-variance / amount-exceeded / quality-hold / missing-data /
unverified-vendor / review-rejected / extraction-failed / Positive-Pay fraud)
routes through :func:`create_exception` so the outbound-webhook
``exception.raised`` event fires from exactly ONE place — no per-call-site emit
to forget, no partial coverage.

What it does, in order:
  1. constructs the ``Exception`` row from the caller's fields,
  2. ``db.add()`` + ``flush()`` so the row has its server-side ``id`` (the
     webhook ``event_key``, so a re-run dedupes on the exception id), and
  3. fires a **best-effort** ``exception.raised`` emit — wrapped so a webhook
     failure can NEVER break exception creation or the invoice mutation that
     triggered it (same contract as the ``transition_invoice`` emit hook). The
     emit itself is a silent no-op when ``AP_WEBHOOKS_ENABLED`` is off.

Callers keep their own dedupe-precheck (each has a different uniqueness rule —
``_ensure_exception`` dedupes on ``(invoice, type, open/escalated)``; Positive
Pay dedupes on ``(type, invoice|description)``); this helper only owns the
construct → flush → emit tail, so it never double-creates.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.exception import Exception as APException

logger = logging.getLogger(__name__)


async def create_exception(
    db: AsyncSession,
    *,
    exception_type: str,
    description: str | None,
    organization_id: uuid.UUID,
    severity: str = "warning",
    status: str = "open",
    invoice=None,
    invoice_id: uuid.UUID | None = None,
    entity_id: uuid.UUID | None = None,
    assigned_to_user_id: uuid.UUID | None = None,
    due_at: datetime | None = None,
) -> APException:
    """Create + persist an ``Exception`` and best-effort emit ``exception.raised``.

    Pass either the loaded ``invoice`` (preferred — the emit payload reads its
    number / vendor name / amount / currency) or a bare ``invoice_id`` (e.g. a
    Positive Pay return where no Invoice is loaded, or ``None`` for an
    invoice-less fraud flag). ``entity_id`` defaults to the invoice's entity
    when an ``invoice`` is supplied.
    """
    resolved_invoice_id = invoice_id if invoice_id is not None else getattr(invoice, "id", None)
    resolved_entity_id = entity_id if entity_id is not None else getattr(invoice, "entity_id", None)

    exc = APException(
        invoice_id=resolved_invoice_id,
        exception_type=exception_type,
        severity=severity,
        description=description,
        status=status,
        organization_id=organization_id,
        entity_id=resolved_entity_id,
        assigned_to_user_id=assigned_to_user_id,
        due_at=due_at,
    )
    db.add(exc)
    # Flush so the row gets its id before we emit (the id is the webhook
    # event_key / dedupe key). Stays within the caller's transaction — a later
    # rollback in the caller still rolls this back too.
    await db.flush()

    try:
        from app.services.webhooks import emit_exception_raised

        await emit_exception_raised(
            organization_id=organization_id,
            exception_id=exc.id,
            exception_type=exception_type,
            severity=severity,
            status=status,
            invoice=invoice,
            invoice_id=resolved_invoice_id,
        )
    except Exception:  # noqa: BLE001 — an emit must never break exception creation
        logger.exception(
            "exception.raised webhook emit failed for org=%s exception=%s",
            organization_id,
            exc.id,
        )

    return exc
