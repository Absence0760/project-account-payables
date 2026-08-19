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

import asyncio
import io
import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

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
from app.services.approval_signature import (
    VERDICT_INVALID,
    VERDICT_UNSIGNED,
    VERDICT_VALID,
    check_approval_row,
)
from app.services.audit_access import log_access
from app.services.audit_dispatch import dispatch_audit
from app.services.audit_report_pdf import AuditReportContext, render_audit_report_pdf
from app.services.branding import get_brand_context
from app.services.report_export import safe_csv_writer
from app.tenant import get_tenant_db
from app.utils.dates import utc_today

router = APIRouter(prefix="/audit", tags=["audit"])


async def _actor_names(
    control_db: AsyncSession, actor_ids: set[uuid.UUID]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({actor_id: name}, {actor_id: email}) for a set of actor ids."""
    names: dict[str, str] = {}
    emails: dict[str, str] = {}
    if actor_ids:
        result = await control_db.execute(select(User).where(User.id.in_(actor_ids)))
        for u in result.scalars().all():
            names[str(u.id)] = u.full_name
            emails[str(u.id)] = u.email
    return names, emails


async def _resolve_actors(
    control_db: AsyncSession, entries: list[AuditLog]
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ({actor_id: name}, {actor_id: email}) from the control DB."""
    return await _actor_names(control_db, {e.actor_id for e in entries if e.actor_id})


def _entries_to_csv(entries: list[AuditExportEntry]) -> str:
    """Render export entries to CSV.

    Cells are written only from the already-sanitised export entries (the
    ``details`` column was scrubbed of regulated values at write time), so no
    banking/tax-id value can reach the CSV surface.
    """
    buf = io.StringIO()
    writer = safe_csv_writer(buf)
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
        filename = f"audit_export_{utc_today().isoformat()}.csv"
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
            brand=get_brand_context(org.settings if org else None),
        )
        pdf_bytes = await asyncio.to_thread(render_audit_report_pdf, ctx)
        filename = f"audit_report_{utc_today().isoformat()}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return export


@router.get("/verify-signatures")
async def verify_signatures_for_period(
    start: date | None = Query(None),
    end: date | None = Query(None),
    limit: int = Query(100, ge=1, le=1000, description="Cap on the findings list"),
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_CFO)),
):
    """Population-level non-repudiation test over a date range.

    The per-invoice check (``/audit/invoice/{id}/verify-signatures``) can only
    answer "is THIS approval still intact" — which presumes you already know
    which invoice to suspect. Testing the control the way an auditor actually
    tests it (over a period's whole population of approvals) had no surface at
    all, so a tampered ``invoice.approved`` row was in practice undetectable.

    This sweeps every ``invoice.approved`` audit row in ``start``..``end``,
    re-derives each signature against its invoice's **current** exact amount via
    the same ``check_approval_row`` primitive, and returns population counts plus
    a bounded list of the rows that did not verify. A clean run — ``invalid`` and
    ``unsigned`` both zero — is the evidence; a non-zero count names exactly the
    rows to investigate.

    Rows are streamed (``yield_per``), so the counts cover the whole population
    without materialising it. ``findings`` is capped by ``limit`` and flags
    ``findings_truncated`` when there were more; the counts are never truncated.

    ``unsigned`` is reported separately from ``invalid``: an approval written
    before ``FEOH_APPROVAL_SIGNING_KEY`` was configured has nothing to verify and
    is not evidence of tampering — but once signing IS configured, an unsigned
    approval in the period is itself a finding, which is why it is listed rather
    than merely counted.

    Admin/CFO only (the auditor privilege), and itself audited (``audit.viewed``
    carrying counts only — no invoice values enter the trail).
    """
    if start is None and end is None:
        raise HTTPException(status_code=400, detail="Provide a start and/or end date")
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail="Invalid range")

    # Join on `correlation_id`: it is UNIQUE on `invoices`, so one audit row
    # maps to exactly one invoice — and it is the column the trail is filed
    # under, so this is the same linkage the per-invoice read uses.
    query = (
        select(
            AuditLog.id,
            AuditLog.actor_id,
            AuditLog.created_at,
            AuditLog.details,
            Invoice.id,
            Invoice.invoice_number,
            Invoice.amount,
        )
        .join(Invoice, Invoice.correlation_id == AuditLog.correlation_id)
        .where(AuditLog.action == "invoice.approved")
        .order_by(AuditLog.created_at)
    )
    if start is not None:
        query = query.where(AuditLog.created_at >= datetime.combine(start, time.min, tzinfo=UTC))
    if end is not None:
        # Inclusive of the whole `end` day (mirrors /audit/export).
        query = query.where(
            AuditLog.created_at < datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC)
        )

    # Keyed off the verdict constants themselves so a renamed/added verdict is a
    # KeyError at test time, never a silently-dropped row.
    counts = dict.fromkeys((VERDICT_VALID, VERDICT_INVALID, VERDICT_UNSIGNED), 0)
    invoice_ids: set[uuid.UUID] = set()
    findings: list[dict] = []
    findings_truncated = False

    signing_key = settings.approval_signing_key
    result = await db.stream(query.execution_options(yield_per=500))
    async for row_id, actor_id, _created_at, details, inv_id, inv_number, inv_amount in result:
        invoice_ids.add(inv_id)
        check = check_approval_row(
            details=details,
            invoice_id=inv_id,
            # Money stays exact — the digest was computed over a Decimal.
            amount=Decimal(str(inv_amount or 0)),
            actor_id=actor_id,
            signing_key=signing_key,
        )
        counts[check.verdict] += 1
        if check.verdict == VERDICT_VALID:
            continue
        if len(findings) >= limit:
            findings_truncated = True
            continue
        findings.append(
            {
                "invoice_id": str(inv_id),
                "invoice_number": inv_number,
                "audit_row_id": str(row_id),
                "actor_id": str(actor_id) if actor_id else None,
                "signed_at": check.signed_at,
                "verdict": check.verdict,
            }
        )

    names, _emails = await _actor_names(
        control_db, {uuid.UUID(f["actor_id"]) for f in findings if f["actor_id"]}
    )
    for finding in findings:
        finding["actor"] = names.get(finding["actor_id"]) if finding["actor_id"] else None

    checked = sum(counts.values())
    await log_access(
        db,
        user=user,
        organization_id=user.organization_id,
        entity_type="audit",
        entity_id=user.organization_id,
        extra={
            "scope": "range",
            "verify_signatures": checked,
            "invalid": counts[VERDICT_INVALID],
            "unsigned": counts[VERDICT_UNSIGNED],
        },
    )
    await db.commit()

    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "signing_configured": bool(signing_key),
        "invoices_covered": len(invoice_ids),
        "approvals_checked": checked,
        "valid": counts[VERDICT_VALID],
        "invalid": counts[VERDICT_INVALID],
        "unsigned": counts[VERDICT_UNSIGNED],
        "findings": findings,
        "findings_truncated": findings_truncated,
    }


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
        # One shared definition of "does this row still verify" — the population
        # sweep below calls the same primitive, so the two can't drift on what
        # counts as unsigned vs tampered.
        check = check_approval_row(
            details=row.details,
            invoice_id=invoice.id,
            amount=current_amount,
            actor_id=row.actor_id,
            signing_key=settings.approval_signing_key,
        )
        results.append(
            {
                "audit_row_id": str(row.id),
                "signed_at": check.signed_at,
                "actor": names.get(str(row.actor_id)) if row.actor_id else None,
                "signed": check.signed,
                "valid": check.valid,
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
