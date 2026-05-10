"""Bulk GL re-coding service.

Re-applies GL codes to a scoped set of invoices using two strategies:

  1. **Vendor priors** (free, fast, idempotent) — looks up the cached
     `gl_account` correction for each invoice's vendor. Applies it
     when it validates against the active chart.

  2. **AI fallback** (billed, opt-in) — for invoices with no usable
     prior, re-fetches the file from S3 and runs the configured
     extraction adapter end-to-end. Used only when the caller opts
     in via `include_ai_fallback=True`.

The AI fallback path produces an `ExtractionUsage` row on the control-
plane DB for every invoice it touches, identical to the regular
extraction flow (see `services.extraction`). Audit log entries are
written for each persisted change so the activity is traceable in the
invoice's history.

Eligibility
-----------
Invoices in `IMMUTABLE_STATUSES` (sending_to_erp through paid) are
excluded — once an invoice has left the AP team's hands, re-coding it
would create reconciliation drift with the ERP.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.gl_account import GLAccount
from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor_priors import VendorExtractionPrior

if TYPE_CHECKING:
    from datetime import date

logger = logging.getLogger(__name__)

# Same set as `api/invoices.py:IMMUTABLE_STATUSES`. Keep in sync — the
# eligibility contract has to match the rest of the bulk-write surface.
_IMMUTABLE_STATUSES: frozenset[InvoiceStatus] = frozenset(
    {
        InvoiceStatus.sending_to_erp,
        InvoiceStatus.sent_to_erp,
        InvoiceStatus.posted_in_erp,
        InvoiceStatus.payment_scheduled,
        InvoiceStatus.paid,
        InvoiceStatus.done,
    }
)


@dataclass
class RecodeFilter:
    from_date: date | None = None
    to_date: date | None = None
    vendor_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass
class RecodeChange:
    invoice_id: uuid.UUID
    invoice_number: str
    vendor_name: str
    old_gl: str | None
    new_gl: str
    source: str  # "vendor_prior" | "ai"


@dataclass
class RecodeReport:
    matched: int = 0
    skipped_immutable: int = 0
    skipped_no_vendor: int = 0
    skipped_no_change: int = 0
    skipped_no_prior_no_ai: int = 0
    skipped_ai_failed: int = 0
    skipped_invalid_code: int = 0
    by_source: dict[str, int] = field(default_factory=lambda: {"vendor_prior": 0, "ai": 0})
    changes: list[RecodeChange] = field(default_factory=list)
    dry_run: bool = True

    def as_dict(self) -> dict:
        return {
            "matched": self.matched,
            "would_change" if self.dry_run else "applied": len(self.changes),
            "by_source": dict(self.by_source),
            "skipped": {
                "immutable_status": self.skipped_immutable,
                "no_vendor": self.skipped_no_vendor,
                "no_change": self.skipped_no_change,
                "no_prior_no_ai": self.skipped_no_prior_no_ai,
                "ai_failed": self.skipped_ai_failed,
                "invalid_code": self.skipped_invalid_code,
            },
            "changes": [
                {
                    "invoice_id": str(c.invoice_id),
                    "invoice_number": c.invoice_number,
                    "vendor_name": c.vendor_name,
                    "old_gl": c.old_gl,
                    "new_gl": c.new_gl,
                    "source": c.source,
                }
                for c in self.changes
            ],
            "dry_run": self.dry_run,
        }


async def _load_active_chart(db: AsyncSession, organization_id: uuid.UUID) -> set[str]:
    rows = (
        (
            await db.execute(
                select(GLAccount.code).where(
                    GLAccount.organization_id == organization_id,
                    GLAccount.is_active == True,  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


async def _load_priors(db: AsyncSession, vendor_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Return {vendor_id: gl_account_string} for vendors that have a
    cached gl_account prior. Vendors without a prior are absent from
    the dict."""
    if not vendor_ids:
        return {}

    rows = (
        await db.execute(
            select(VendorExtractionPrior.vendor_id, VendorExtractionPrior.value).where(
                VendorExtractionPrior.vendor_id.in_(vendor_ids),
                VendorExtractionPrior.field_name == "gl_account",
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def _select_eligible(
    db: AsyncSession,
    organization_id: uuid.UUID,
    filt: RecodeFilter,
) -> list[Invoice]:
    """Resolve filter → list of eligible Invoice rows.

    `from_date` / `to_date` filter on `invoice_date` (the natural date
    a user thinks of for "the May invoices"). Falls back to including
    rows with NULL `invoice_date` only when neither bound is given —
    otherwise a date-bounded request would silently sweep up undated
    rows that the caller didn't intend.
    """
    q = select(Invoice).where(Invoice.organization_id == organization_id)
    if filt.from_date is not None:
        q = q.where(Invoice.invoice_date >= filt.from_date)
    if filt.to_date is not None:
        q = q.where(Invoice.invoice_date <= filt.to_date)
    if filt.vendor_ids:
        q = q.where(Invoice.vendor_id.in_(filt.vendor_ids))

    rows = (await db.execute(q)).scalars().all()
    return list(rows)


async def bulk_recode_gl(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    filt: RecodeFilter,
    include_ai_fallback: bool = False,
    dry_run: bool = True,
    actor_id: uuid.UUID | None = None,
    org_settings: dict | None = None,
    ctrl_db: AsyncSession | None = None,
    ai_runner=None,
) -> RecodeReport:
    """Run the bulk GL re-code pass. See module docstring for details.

    `ai_runner` is an optional injected coroutine (signature:
    `async (db, invoice, *, actor_id, org_settings, ctrl_db) -> None`)
    used when `include_ai_fallback=True`. Defaults to
    `services.extraction.run_extraction`. Tests inject a fake to avoid
    spinning up the real S3 / vision-adapter machinery.
    """
    report = RecodeReport(dry_run=dry_run)

    active_chart = await _load_active_chart(db, organization_id)
    candidates = await _select_eligible(db, organization_id, filt)

    eligible: list[Invoice] = []
    for inv in candidates:
        if inv.status in _IMMUTABLE_STATUSES:
            report.skipped_immutable += 1
            continue
        if inv.vendor_id is None:
            report.skipped_no_vendor += 1
            continue
        eligible.append(inv)

    report.matched = len(eligible)

    vendor_ids = {inv.vendor_id for inv in eligible if inv.vendor_id}
    priors = await _load_priors(db, vendor_ids)

    # First pass: priors-only. Cheap, free, hits as many invoices as the
    # learned cache covers.
    needs_ai: list[Invoice] = []
    for inv in eligible:
        prior_code = priors.get(inv.vendor_id)
        if prior_code is None:
            needs_ai.append(inv)
            continue

        if active_chart and prior_code not in active_chart:
            # Cached value is now stale — don't apply, but try AI fallback
            # if the operator opted in. Otherwise count as invalid.
            report.skipped_invalid_code += 1
            needs_ai.append(inv)
            continue

        if inv.gl_account == prior_code:
            report.skipped_no_change += 1
            continue

        report.changes.append(
            RecodeChange(
                invoice_id=inv.id,
                invoice_number=inv.invoice_number,
                vendor_name=inv.vendor_name or "",
                old_gl=inv.gl_account,
                new_gl=prior_code,
                source="vendor_prior",
            )
        )
        report.by_source["vendor_prior"] += 1
        if not dry_run:
            inv.gl_account = prior_code

    # Second pass: AI fallback for invoices with no usable prior.
    if include_ai_fallback and needs_ai:
        if ai_runner is None:
            from app.services.extraction import run_extraction as _runner

            ai_runner = _runner

        for inv in needs_ai:
            old_gl = inv.gl_account
            try:
                # Reuses the full extraction pipeline (chart-of-accounts
                # injection + RAG + post-extraction validation), so the
                # AI re-code lands inside the same guardrails as a fresh
                # upload. Persists usage to ctrl_db when provided.
                await ai_runner(
                    db,
                    inv,
                    actor_id=actor_id,
                    org_settings=org_settings,
                    ctrl_db=ctrl_db,
                )
            except Exception:
                logger.exception("AI re-code failed for invoice %s", inv.id)
                report.skipped_ai_failed += 1
                continue

            # `run_extraction` may have updated `inv.gl_account` directly.
            new_gl = inv.gl_account
            if new_gl is None or new_gl == old_gl:
                report.skipped_no_change += 1
                if dry_run:
                    # Roll back the speculative AI write so dry-run is
                    # honest about not persisting anything. Not perfect —
                    # `run_extraction` may have inserted line items / RAG
                    # / vendor-prior side effects, so dry-run + AI is
                    # documented as "exploratory only".
                    inv.gl_account = old_gl
                continue

            report.changes.append(
                RecodeChange(
                    invoice_id=inv.id,
                    invoice_number=inv.invoice_number,
                    vendor_name=inv.vendor_name or "",
                    old_gl=old_gl,
                    new_gl=new_gl,
                    source="ai",
                )
            )
            report.by_source["ai"] += 1
            if dry_run:
                inv.gl_account = old_gl  # see comment above

    elif needs_ai and not include_ai_fallback:
        report.skipped_no_prior_no_ai += len(needs_ai)

    # Audit log + commit when persisting.
    if not dry_run and report.changes:
        from app.services.audit import log_action

        for change in report.changes:
            await log_action(
                db,
                correlation_id=uuid.uuid4(),
                organization_id=organization_id,
                actor_id=actor_id,
                action="invoice.gl_recoded",
                entity_type="invoice",
                entity_id=change.invoice_id,
                details={
                    "old_gl": change.old_gl,
                    "new_gl": change.new_gl,
                    "source": change.source,
                    "bulk_run_at": datetime.now(UTC).isoformat(),
                },
            )

        await db.commit()

    return report
