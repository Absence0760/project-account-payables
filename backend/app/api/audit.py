"""Auditor-facing audit-trail endpoints (`/api/audit`).

SOX evidence surface for external auditors: per-invoice and date-range export
of the tenant ``audit_log``, in JSON or CSV. Distinct from the per-invoice
``GET /api/invoices/{id}/audit-log`` (operational UI) — this router carries its
own RBAC (export is admin/CFO only, the auditor-equivalent privilege) and is
itself audited (every export writes an ``audit.exported`` row).

All routes are reads — no money moves — so the idempotency invariant does not
bind here. Every audit query resolves the tenant DB via ``get_tenant_db``;
actor names/emails are resolved against the control DB exactly as the existing
endpoint does. No PUT/PATCH/DELETE is defined anywhere in this router, keeping
the audit trail GET-only.
"""

import csv
import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    require_roles,
)
from app.config import settings
from app.database import get_control_db
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.user import User
from app.models.workflow import AuditLog
from app.schemas.audit import AuditExportEntry
from app.services.approval_signature import verify_approval
from app.services.audit_access import log_access
from app.services.audit_dispatch import dispatch_audit
from app.services.audit_report_pdf import AuditReportContext, render_audit_report_pdf
from app.tenant import get_tenant_db

router = APIRouter(prefix="/audit", tags=["audit"])


async def _resolve_actors(
    control_db: AsyncSession, entries: list[AuditLog]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({actor_id: name}, {actor_id: email}) from the control DB."""
    actor_ids = {e.actor_id for e in entries if e.actor_id}
    names: dict[str, str] = {}
    emails: dict[str, str] = {}
    if actor_ids:
        result = await control_db.execute(select(User).where(User.id.in_(actor_ids)))
        for u in result.scalars().all():
            names[str(u.id)] = u.full_name
            emails[str(u.id)] = u.email
    return names, emails


def _entries_to_csv(entries: list[AuditExportEntry]) -> str:
    """Render export entries to CSV.

    Cells are written only from the already-sanitised export entries (the
    ``details`` column was scrubbed of regulated values at write time), so no
    banking/tax-id value can reach the CSV surface.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "created_at",
            "action",
            "entity_type",
            "entity_id",
            "actor_name",
            "actor_email",
            "correlation_id",
            "details",
        ]
    )
    for e in entries:
        writer.writerow(
            [
                e.created_at,
                e.action,
                e.entity_type,
                e.entity_id or "",
                e.actor_name or "",
                e.actor_email or "",
                e.correlation_id,
                "" if e.details is None else str(e.details),
            ]
        )
    return buf.getvalue()


@router.get("/export")
async def export_audit_trail(
    invoice_id: uuid.UUID | None = Query(None),
    start: date | None = Query(None),
    end: date | None = Query(None),
    entity_type: str | None = Query(None),
    export_format: str = Query("json", alias="format", pattern="^(json|csv|pdf)$"),
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
):
    """Auditor export — per-invoice OR date-range, JSON / CSV / PDF.

    Provide either ``invoice_id`` (the invoice's whole correlation trail) or a
    ``start``/``end`` date range. The two are mutually exclusive. Validation
    errors are generic ("invalid range") and never echo entity values; a
    missing invoice returns 404 with no leaked data. The export itself is
    audited (``audit.exported``).

    ``format=pdf`` returns a formatted SOX audit-trail report (cover + summary +
    chronological table) for external auditors. It renders exactly the same
    already-sanitised export entries the JSON/CSV dialects do (PII is kept out of
    ``details`` at audit-write time) — no broader, no regulated value added.
    """
    if invoice_id is not None and (start is not None or end is not None):
        raise HTTPException(status_code=400, detail="Provide invoice_id or a date range, not both")
    if invoice_id is None and start is None and end is None:
        raise HTTPException(status_code=400, detail="Provide invoice_id or a start/end date range")

    query = select(AuditLog)

    if invoice_id is not None:
        row = (
            await db.execute(
                select(Invoice.correlation_id, Invoice.invoice_number).where(
                    Invoice.id == invoice_id
                )
            )
        ).first()
        if not row or not row[0]:
            raise HTTPException(status_code=404, detail="Invoice not found")
        correlation_id, invoice_number = row
        query = query.where(AuditLog.correlation_id == correlation_id)
        scope = "invoice"
        scope_label = f"Invoice {invoice_number}" if invoice_number else "Invoice"
    else:
        if start is not None and end is not None and start > end:
            raise HTTPException(status_code=400, detail="Invalid range")
        if start is not None:
            query = query.where(
                AuditLog.created_at >= datetime.combine(start, datetime.min.time(), tzinfo=UTC)
            )
        if end is not None:
            # Inclusive of the whole `end` day: strictly-less-than the start of
            # the next day, so a row at end-of-day's last microsecond is included.
            next_day_start = datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
            query = query.where(AuditLog.created_at < next_day_start)
        scope = "range"
        scope_label = (
            f"{start.isoformat() if start else 'beginning'} to {end.isoformat() if end else 'now'}"
        )

    if entity_type:
        query = query.where(AuditLog.entity_type == entity_type)

    entries = (await db.execute(query.order_by(AuditLog.created_at))).scalars().all()
    names, emails = await _resolve_actors(control_db, entries)
    export = [AuditExportEntry.from_db(e, names, emails) for e in entries]

    # The export is itself an auditable access event. Use the export-specific
    # verb ("audit.exported", not "audit.viewed"), so dispatch directly. The
    # details carry only non-PII scope metadata (scope + row count).
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=user.organization_id,
        actor_id=user.id,
        action="audit.exported",
        entity_type="audit",
        entity_id=invoice_id or user.organization_id,
        details={"scope": scope, "count": len(export), "format": export_format},
    )
    await db.commit()

    if export_format == "csv":
        filename = f"audit_export_{date.today().isoformat()}.csv"
        return Response(
            content=_entries_to_csv(export),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    if export_format == "pdf":
        org = await control_db.get(Organization, user.organization_id)
        ctx = AuditReportContext(
            org_name=(org.name if org else "Organization"),
            scope=scope,
            scope_label=scope_label,
            generated_at=datetime.now(UTC),
            generated_by_name=user.full_name,
            generated_by_email=user.email,
            entries=export,
        )
        pdf_bytes = render_audit_report_pdf(ctx)
        filename = f"audit_report_{date.today().isoformat()}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return export


@router.get("/invoice/{invoice_id}")
async def get_invoice_audit_trail(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Auditor-facing per-invoice trail (alias of the operational endpoint).

    Same correlation-scoped query as ``GET /api/invoices/{id}/audit-log`` but
    in its own ``/api/audit`` namespace with the auditor RBAC set. Ordered by
    ``created_at`` so the timeline is chronological. The view is itself audited.
    """
    correlation_id = (
        await db.execute(select(Invoice.correlation_id).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if not correlation_id:
        raise HTTPException(status_code=404, detail="Invoice not found")

    entries = (
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.correlation_id == correlation_id)
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )
    names, emails = await _resolve_actors(control_db, entries)
    export = [AuditExportEntry.from_db(e, names, emails) for e in entries]

    await log_access(
        db,
        user=user,
        organization_id=user.organization_id,
        entity_type="audit",
        entity_id=invoice_id,
        correlation_id=correlation_id,
    )
    await db.commit()

    return export


@router.get("/invoice/{invoice_id}/verify-signatures")
async def verify_invoice_signatures(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
):
    """Cryptographic non-repudiation check on an invoice's approval signatures.

    Loads every ``invoice.approved`` audit row carrying a ``details.signature``
    block and re-derives the HMAC-SHA256 over the approval facts — invoice id +
    the invoice's CURRENT exact ``amount`` + the row's ``actor_id`` + decision +
    the signed timestamp — comparing it (constant-time) to the stored digest. A
    tampered amount, a swapped actor, or an altered timestamp flips ``valid`` to
    ``False``, proving the approval record wasn't silently changed after the
    fact.

    Admin/CFO only (the auditor privilege). This is a sensitive read, so it
    writes its own ``audit.viewed`` access row.
    """
    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    rows = (
        (
            await db.execute(
                select(AuditLog)
                .where(
                    AuditLog.correlation_id == invoice.correlation_id,
                    AuditLog.action == "invoice.approved",
                )
                .order_by(AuditLog.created_at)
            )
        )
        .scalars()
        .all()
    )

    # Recompute against the invoice's CURRENT amount — the whole point is that a
    # post-approval tamper of the amount breaks verification. Money stays
    # Decimal (never float).
    current_amount = Decimal(str(invoice.amount or 0))

    names, _emails = await _resolve_actors(control_db, rows)

    results: list[dict] = []
    for row in rows:
        sig = (row.details or {}).get("signature") if row.details else None
        if not sig or not isinstance(sig, dict):
            # An approval row written before signing was enabled has no block —
            # report it as unsigned rather than invalid (nothing to verify).
            results.append(
                {
                    "audit_row_id": str(row.id),
                    "signed_at": None,
                    "actor": names.get(str(row.actor_id)) if row.actor_id else None,
                    "signed": False,
                    "valid": False,
                }
            )
            continue

        signed_at_raw = sig.get("signed_at")
        signed_at = None
        if signed_at_raw:
            try:
                signed_at = datetime.fromisoformat(signed_at_raw)
            except (ValueError, TypeError):
                signed_at = None

        valid = False
        if signed_at is not None and row.actor_id is not None:
            try:
                valid = verify_approval(
                    invoice_id=invoice.id,
                    amount=current_amount,
                    actor_id=row.actor_id,
                    decision="approved",
                    timestamp=signed_at,
                    signature=sig.get("value"),
                    signing_key=settings.approval_signing_key,
                )
            except (InvalidOperation, ValueError):
                valid = False

        results.append(
            {
                "audit_row_id": str(row.id),
                "signed_at": signed_at_raw,
                "actor": names.get(str(row.actor_id)) if row.actor_id else None,
                "signed": True,
                "valid": valid,
            }
        )

    # Sensitive read → access audit. No PII in the details (counts only).
    await log_access(
        db,
        user=user,
        organization_id=user.organization_id,
        entity_type="audit",
        entity_id=invoice_id,
        correlation_id=invoice.correlation_id,
        extra={"verify_signatures": len(results)},
    )
    await db.commit()

    return {
        "invoice_id": str(invoice_id),
        "signing_configured": bool(settings.approval_signing_key),
        "approvals": results,
    }
