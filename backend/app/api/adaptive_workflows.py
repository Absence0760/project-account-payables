"""Adaptive AI workflows — approval-pattern learning, baseline anomaly
detection, and advisory workflow-change suggestions.

All three surfaces are **read models** over the tenant's approval history; the
statistics are deterministic (no LLM, no cloud key) and computed by the pure
functions in ``app.services.adaptive_workflows``. This file does the SQL, the
control-plane name join, the response shaping, and the suggestion persistence.

Boundaries (see ``backend/docs/adaptive-workflows.md``):
  * The anomaly endpoint is **read-only** — it never writes warnings or
    Exception rows (that is ``invoice_warnings.fraud_stat_anomaly``'s job, a
    complementary per-invoice surface). It returns the per-vendor *baseline* it
    compared against, for explainability.
  * Suggestions are **advisory** — ``status='applied'`` is reserved for a
    future explicit admin action that routes through the audited approval /
    workflow-definition path. Nothing here applies anything.
  * ``GET /suggestions`` performs an idempotent upsert (write-on-GET) so a
    dismissal is durable across recomputation; it moves no money and is not an
    auditable status change, so no ``audit_log`` row is written.
  * Dismissing a suggestion mutates only the advisory row's status — not an
    invoice / payment / approval / vendor — so the audit-write invariant does
    not apply.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_control_db,
    require_roles,
)
from app.models.adaptive_suggestion import WorkflowSuggestion
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.user import Role, User, UserRole
from app.models.workflow import AuditLog
from app.schemas.adaptive_workflows import (
    AnomalyBatchResponse,
    ApprovalPatternsResponse,
    InvoiceAnomalyResponse,
    RoutingSuggestionResponse,
    SuggestionListResponse,
)
from app.services.adaptive_workflows import (
    DerivedSuggestion,
    EligibleApprover,
    InvoiceAnomaly,
    RoutingSuggestion,
    VendorBaseline,
    _decimal_days,
    compute_approver_patterns,
    compute_vendor_baseline,
    compute_vendor_patterns,
    derive_suggestions,
    detect_invoice_anomaly,
    recommend_approvers,
)
from app.tenant import apply_entity_scope, get_entity_id, get_tenant, get_tenant_db

router = APIRouter(prefix="/adaptive", tags=["adaptive-workflows"])

_READ_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)
_WRITE_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER)
# Roles that can actually act on an approval — the eligible-approver pool for
# smart routing. ap_clerk enters invoices but does not approve, so it's excluded.
_APPROVAL_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)

# Approved-or-beyond — the "historically accepted" set used for the vendor
# baseline (pending / rejected invoices are not part of the accepted norm).
_APPROVED_STATUSES = (
    InvoiceStatus.approved,
    InvoiceStatus.sending_to_erp,
    InvoiceStatus.sent_to_erp,
    InvoiceStatus.posted_in_erp,
    InvoiceStatus.payment_scheduled,
    InvoiceStatus.paid,
    InvoiceStatus.done,
)
_IN_REVIEW_STATUSES = (
    InvoiceStatus.new,
    InvoiceStatus.pending,
    InvoiceStatus.ready_for_review,
)


def _adaptive_settings(org: Organization) -> dict:
    """Merge org overrides over the defaults. ``settings.adaptive`` may omit
    keys to inherit; unknown keys are dropped (mirrors the fraud-rules pattern).
    """
    defaults = {
        "sigma": Decimal("2.0"),
        "median_multiple": Decimal("3.0"),
        "timing_multiple": Decimal("3.0"),
        "min_history": 5,
        "suggestion_min_history": 12,
        "suggestion_min_consistency_pct": Decimal("95"),
    }
    overrides = (org.settings or {}).get("adaptive") or {}
    merged = dict(defaults)
    for k, v in overrides.items():
        if k not in merged:
            continue
        if k == "min_history" or k == "suggestion_min_history":
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
# Shared: pull approval/rejection decision rows from the tenant audit log
# ---------------------------------------------------------------------------


async def _ready_for_review_starts(
    db: AsyncSession, *, since: datetime, entity_id: uuid.UUID | None
) -> dict[uuid.UUID, datetime]:
    """Earliest ``ready_for_review`` transition timestamp per invoice id (the
    time-to-approve clock start) within the ``since`` lookback window. Read from
    the audit row whose ``details->>'new_status' == 'ready_for_review'``.

    The ``since`` bound keeps the clock-start inside the same window the
    decision rows are pulled from, so a ``ready_for_review`` transition that
    predates the lookback can't be paired with an in-window approval to produce
    a time-to-approve that spans outside the window."""
    q = (
        select(
            AuditLog.entity_id,
            func.min(AuditLog.created_at),
        )
        .where(
            AuditLog.entity_type == "invoice",
            AuditLog.details["new_status"].astext == "ready_for_review",
            AuditLog.created_at >= since,
        )
        .group_by(AuditLog.entity_id)
    )
    rows = (await db.execute(q)).all()
    return {eid: ts for eid, ts in rows if eid is not None}


async def _decision_rows(
    db: AsyncSession, *, since: datetime, entity_id: uuid.UUID | None
) -> list[dict]:
    """Build the duck-typed decision rows the pure-stat layer consumes from the
    audit_log ⋈ invoices join."""
    q = (
        select(
            AuditLog.action,
            AuditLog.actor_id,
            AuditLog.created_at,
            AuditLog.details,
            Invoice.id,
            Invoice.vendor_id,
            Invoice.vendor_name,
            Invoice.amount,
            Invoice.created_at.label("inv_created_at"),
        )
        .join(Invoice, Invoice.id == AuditLog.entity_id)
        .where(
            AuditLog.entity_type == "invoice",
            AuditLog.action.in_(("invoice.approved", "invoice.rejected")),
            AuditLog.created_at >= since,
        )
    )
    q = apply_entity_scope(q, Invoice, entity_id)
    rows = (await db.execute(q)).all()

    starts = await _ready_for_review_starts(db, since=since, entity_id=entity_id)

    decisions: list[dict] = []
    for row in rows:
        action, actor_id, created_at, details = row[0], row[1], row[2], row[3]
        inv_id, vendor_id, vendor_name, amount, inv_created = row[4], row[5], row[6], row[7], row[8]
        decision = "approved" if action == "invoice.approved" else "rejected"
        unmodified = decision == "approved" and not (details or {}).get("changes")
        ttd: Decimal | None = None
        if decision == "approved":
            clock_start = starts.get(inv_id) or inv_created
            if clock_start is not None and created_at is not None:
                # Clamp ≥ 0: out-of-order / backfilled audit rows can put the
                # approval before the clock start, which would otherwise feed a
                # negative day-count into the median/baseline.
                ttd = max(Decimal("0"), _decimal_days(created_at - clock_start))
        decisions.append(
            {
                "approver_id": str(actor_id) if actor_id else None,
                "vendor_id": str(vendor_id) if vendor_id else None,
                "vendor_name": vendor_name or "",
                "amount": Decimal(str(amount or 0)),
                "decision": decision,
                "unmodified": unmodified,
                "time_to_approve_days": ttd,
            }
        )
    return decisions


async def _approver_names(
    ctrl_db: AsyncSession, approver_ids: set[str], *, organization_id: uuid.UUID
) -> dict[str, str]:
    """Map approver UUID-strings → display name from the control plane.

    Scoped to ``organization_id`` so a name can only ever resolve from the
    caller's own org — matching how the rest of the codebase reads the
    control-plane User table, even though the ids originate from this tenant's
    own audit_log and thus already belong to this org."""
    ids: list[uuid.UUID] = []
    for s in approver_ids:
        if s == "unknown":
            continue
        try:
            ids.append(uuid.UUID(s))
        except ValueError:
            continue
    if not ids:
        return {}
    rows = (
        await ctrl_db.execute(
            select(User.id, User.full_name).where(
                User.id.in_(ids),
                User.organization_id == organization_id,
            )
        )
    ).all()
    return {str(uid): name for uid, name in rows}


# ---------------------------------------------------------------------------
# GET /api/adaptive/approval-patterns
# ---------------------------------------------------------------------------


@router.get("/approval-patterns", response_model=ApprovalPatternsResponse)
async def approval_patterns(
    days: int = Query(180, ge=1, le=730),
    db: AsyncSession = Depends(get_tenant_db),
    ctrl_db: AsyncSession = Depends(get_control_db),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_READ_ROLES)),
):
    since = datetime.now(UTC) - timedelta(days=days)
    decisions = await _decision_rows(db, since=since, entity_id=entity_id)

    approver_ids = {d["approver_id"] or "unknown" for d in decisions}
    names = await _approver_names(ctrl_db, approver_ids, organization_id=user.organization_id)

    approvers = compute_approver_patterns(decisions, names=names)
    vendors = compute_vendor_patterns(decisions)

    return ApprovalPatternsResponse(
        generated_at=datetime.now(UTC).isoformat(),
        lookback_days=days,
        entity_id=str(entity_id) if entity_id else None,
        approvers=[
            {
                "approver_id": a.approver_id,
                "approver_name": a.approver_name,
                "approved_count": a.approved_count,
                "rejected_count": a.rejected_count,
                "approval_rate_pct": str(a.approval_rate_pct),
                "median_time_to_approve_days": str(a.median_time_to_approve_days),
                "avg_time_to_approve_days": str(a.avg_time_to_approve_days),
                "sample_size": a.sample_size,
            }
            for a in approvers
        ],
        vendors=[
            {
                "vendor_id": v.vendor_id,
                "vendor_name": v.vendor_name,
                "approved_count": v.approved_count,
                "rejected_count": v.rejected_count,
                "approval_rate_pct": str(v.approval_rate_pct),
                "unmodified_count": v.unmodified_count,
                "consistency_pct": str(v.consistency_pct),
                "avg_approved_amount": str(v.avg_approved_amount),
                "median_approved_amount": str(v.median_approved_amount),
                "min_approved_amount": str(v.min_approved_amount),
                "max_approved_amount": str(v.max_approved_amount),
                "sample_size": v.sample_size,
            }
            for v in vendors
        ],
    )


# ---------------------------------------------------------------------------
# GET /api/adaptive/anomalies
# ---------------------------------------------------------------------------


async def _vendor_approved_rows(
    db: AsyncSession,
    *,
    vendor_id: uuid.UUID | None,
    vendor_name: str,
    entity_id: uuid.UUID | None,
) -> list[dict]:
    """That vendor's historically-approved invoices, shaped for the baseline.

    The approver + timing per invoice come from the ``invoice.approved`` audit
    row (LEFT joined; an invoice may pre-date the audit instrumentation)."""
    q = select(
        Invoice.id,
        Invoice.amount,
        AuditLog.actor_id,
        AuditLog.created_at,
        Invoice.created_at.label("inv_created_at"),
    ).select_from(Invoice)
    q = q.join(
        AuditLog,
        (AuditLog.entity_id == Invoice.id)
        & (AuditLog.entity_type == "invoice")
        & (AuditLog.action == "invoice.approved"),
        isouter=True,
    ).where(Invoice.status.in_(_APPROVED_STATUSES))
    if vendor_id is not None:
        q = q.where(Invoice.vendor_id == vendor_id)
    else:
        q = q.where(Invoice.vendor_id.is_(None), Invoice.vendor_name == vendor_name)
    q = apply_entity_scope(q, Invoice, entity_id)
    rows = (await db.execute(q)).all()

    starts = await _ready_for_review_starts(
        db, since=datetime.now(UTC) - timedelta(days=3650), entity_id=entity_id
    )
    out: list[dict] = []
    for inv_id, amount, actor_id, approved_at, inv_created in rows:
        ttd: Decimal | None = None
        if approved_at is not None:
            clock_start = starts.get(inv_id) or inv_created
            if clock_start is not None:
                # Clamp ≥ 0 — see _decision_rows.
                ttd = max(Decimal("0"), _decimal_days(approved_at - clock_start))
        out.append(
            {
                "amount": Decimal(str(amount or 0)),
                "approver_id": str(actor_id) if actor_id else None,
                "time_to_approve_days": ttd,
            }
        )
    return out


def _anomaly_dict(a: InvoiceAnomaly) -> dict:
    baseline: VendorBaseline | None = a.baseline
    return {
        "invoice_id": a.invoice_id,
        "vendor_id": a.vendor_id,
        "vendor_name": a.vendor_name,
        "amount": str(a.amount),
        "insufficient_history": a.insufficient_history,
        "baseline": None
        if baseline is None
        else {
            "vendor_id": baseline.vendor_id,
            "vendor_name": baseline.vendor_name,
            "sample_size": baseline.sample_size,
            "mean_amount": str(baseline.mean_amount),
            "median_amount": str(baseline.median_amount),
            "stdev_amount": str(baseline.stdev_amount),
            "min_amount": str(baseline.min_amount),
            "max_amount": str(baseline.max_amount),
            "typical_approver_ids": baseline.typical_approver_ids,
            "median_time_to_approve_days": str(baseline.median_time_to_approve_days),
        },
        "flags": [
            {
                "code": f.code,
                "severity": f.severity,
                "message": f.message,
                "observed": f.observed,
                "expected": f.expected,
            }
            for f in a.flags
        ],
    }


async def _time_in_review_days(
    db: AsyncSession, inv: Invoice, *, entity_id: uuid.UUID | None
) -> Decimal | None:
    """Elapsed days since the invoice entered ``ready_for_review`` (for an
    in-flight invoice). ``None`` when it never did."""
    start = (
        await db.execute(
            select(func.min(AuditLog.created_at)).where(
                AuditLog.entity_type == "invoice",
                AuditLog.entity_id == inv.id,
                AuditLog.details["new_status"].astext == "ready_for_review",
            )
        )
    ).scalar()
    if start is None:
        return None
    # Clamp ≥ 0 — guards against a ready_for_review row stamped in the future.
    return max(Decimal("0"), _decimal_days(datetime.now(UTC) - start))


@router.get("/anomalies")
async def anomalies(
    invoice_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_READ_ROLES)),
):
    cfg = _adaptive_settings(org)
    kwargs = dict(
        sigma=cfg["sigma"],
        median_multiple=cfg["median_multiple"],
        timing_multiple=cfg["timing_multiple"],
        min_history=cfg["min_history"],
    )

    if invoice_id is not None:
        q = apply_entity_scope(select(Invoice).where(Invoice.id == invoice_id), Invoice, entity_id)
        inv = (await db.execute(q)).scalar_one_or_none()
        if inv is None:
            raise HTTPException(status_code=404, detail="Invoice not found")
        approved_rows = await _vendor_approved_rows(
            db, vendor_id=inv.vendor_id, vendor_name=inv.vendor_name, entity_id=entity_id
        )
        baseline = compute_vendor_baseline(
            approved_rows,
            vendor_id=str(inv.vendor_id) if inv.vendor_id else None,
            vendor_name=inv.vendor_name or "",
            min_history=cfg["min_history"],
        )
        tir = await _time_in_review_days(db, inv, entity_id=entity_id)
        anomaly = detect_invoice_anomaly(
            inv,
            baseline,
            proposed_approver_id=str(inv.assigned_to_id) if inv.assigned_to_id else None,
            time_in_review_days=tir,
            **kwargs,
        )
        return InvoiceAnomalyResponse(**_anomaly_dict(anomaly))

    # Batch mode — scan in-review invoices, group by vendor, build each
    # baseline once.
    q = apply_entity_scope(
        select(Invoice)
        .where(Invoice.status.in_(_IN_REVIEW_STATUSES))
        .order_by(Invoice.created_at.desc())
        .limit(200),
        Invoice,
        entity_id,
    )
    invoices = (await db.execute(q)).scalars().all()
    baselines: dict[str, VendorBaseline | None] = {}
    flagged: list[dict] = []
    for inv in invoices:
        vkey = str(inv.vendor_id) if inv.vendor_id else f"name:{inv.vendor_name}"
        if vkey not in baselines:
            approved_rows = await _vendor_approved_rows(
                db,
                vendor_id=inv.vendor_id,
                vendor_name=inv.vendor_name,
                entity_id=entity_id,
            )
            baselines[vkey] = compute_vendor_baseline(
                approved_rows,
                vendor_id=str(inv.vendor_id) if inv.vendor_id else None,
                vendor_name=inv.vendor_name or "",
                min_history=cfg["min_history"],
            )
        tir = await _time_in_review_days(db, inv, entity_id=entity_id)
        anomaly = detect_invoice_anomaly(
            inv,
            baselines[vkey],
            proposed_approver_id=str(inv.assigned_to_id) if inv.assigned_to_id else None,
            time_in_review_days=tir,
            **kwargs,
        )
        if anomaly.flags:
            flagged.append(_anomaly_dict(anomaly))
    return AnomalyBatchResponse(total_scanned=len(invoices), flagged=flagged)


# ---------------------------------------------------------------------------
# GET /api/adaptive/suggestions  (write-on-GET upsert; advisory only)
# ---------------------------------------------------------------------------


def _suggestion_dict(s: WorkflowSuggestion) -> dict:
    return {
        "id": s.id,
        "kind": s.kind,
        "vendor_id": str(s.vendor_id) if s.vendor_id else None,
        "vendor_name": s.vendor_name,
        "title": s.title,
        "rationale": s.rationale,
        "payload": s.payload,
        "confidence_pct": str(s.confidence_pct),
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "dismissed_at": s.dismissed_at.isoformat() if s.dismissed_at else None,
    }


@router.get("/suggestions", response_model=SuggestionListResponse)
async def suggestions(
    status: Literal["open", "dismissed", "applied", "stale", "all"] = Query("open"),
    days: int = Query(365, ge=1, le=730),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_READ_ROLES)),
):
    cfg = _adaptive_settings(org)
    since = datetime.now(UTC) - timedelta(days=days)
    decisions = await _decision_rows(db, since=since, entity_id=entity_id)
    vendor_patterns = compute_vendor_patterns(decisions)
    derived: list[DerivedSuggestion] = derive_suggestions(
        vendor_patterns,
        min_history=cfg["suggestion_min_history"],
        min_consistency_pct=cfg["suggestion_min_consistency_pct"],
    )

    fresh_keys = {d.dedupe_key for d in derived}

    # Upsert each derived suggestion: insert as `open`, or refresh the
    # payload/text/confidence of an existing row WITHOUT touching its status.
    for d in derived:
        stmt = (
            pg_insert(WorkflowSuggestion)
            .values(
                id=uuid.uuid4(),
                organization_id=org.id,
                entity_id=entity_id,
                kind=d.kind,
                dedupe_key=d.dedupe_key,
                vendor_id=uuid.UUID(d.vendor_id) if d.vendor_id else None,
                vendor_name=d.vendor_name,
                title=d.title,
                rationale=d.rationale,
                payload=d.payload,
                confidence_pct=d.confidence_pct,
                status="open",
            )
            .on_conflict_do_update(
                index_elements=[WorkflowSuggestion.dedupe_key],
                set_={
                    "vendor_name": d.vendor_name,
                    "title": d.title,
                    "rationale": d.rationale,
                    "payload": d.payload,
                    "confidence_pct": d.confidence_pct,
                    "updated_at": datetime.now(UTC),
                    # When a previously-stale suggestion holds again, re-open it;
                    # never override a dismissed/applied row.
                    "status": case(
                        (WorkflowSuggestion.status == "stale", "open"),
                        else_=WorkflowSuggestion.status,
                    ),
                },
            )
        )
        await db.execute(stmt)

    # Open rows whose condition no longer holds → stale (keep history; don't
    # delete). Dismissed / applied rows are untouched. Scope to this org to
    # match the insert + dismiss gates (the table carries organization_id).
    open_rows = (
        (
            await db.execute(
                select(WorkflowSuggestion).where(
                    WorkflowSuggestion.organization_id == org.id,
                    WorkflowSuggestion.status == "open",
                )
            )
        )
        .scalars()
        .all()
    )
    for row in open_rows:
        if row.dedupe_key not in fresh_keys:
            row.status = "stale"
            row.updated_at = datetime.now(UTC)
    await db.flush()

    q = (
        select(WorkflowSuggestion)
        .where(WorkflowSuggestion.organization_id == org.id)
        .order_by(WorkflowSuggestion.created_at.desc())
    )
    if status != "all":
        q = q.where(WorkflowSuggestion.status == status)
    rows = (await db.execute(q)).scalars().all()
    return SuggestionListResponse(suggestions=[_suggestion_dict(r) for r in rows])


# ---------------------------------------------------------------------------
# POST /api/adaptive/suggestions/{id}/dismiss
# ---------------------------------------------------------------------------


@router.post("/suggestions/{suggestion_id}/dismiss", response_model=SuggestionListResponse)
async def dismiss_suggestion(
    suggestion_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
):
    row = (
        await db.execute(
            select(WorkflowSuggestion).where(
                WorkflowSuggestion.id == suggestion_id,
                WorkflowSuggestion.organization_id == org.id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    # Idempotent — dismissing an already-dismissed row is a no-op.
    if row.status != "dismissed":
        row.status = "dismissed"
        row.dismissed_by = user.id
        row.dismissed_at = datetime.now(UTC)
        await db.flush()
    return SuggestionListResponse(suggestions=[_suggestion_dict(row)])


# ---------------------------------------------------------------------------
# GET /api/adaptive/routing-suggestion  (advisory smart routing; read-only)
# ---------------------------------------------------------------------------


async def _eligible_approver_ids(
    ctrl_db: AsyncSession, *, organization_id: uuid.UUID
) -> dict[str, str | None]:
    """Active control-plane users in this org holding an approval-capable role →
    {approver_id: full_name}. Scoped to the caller's org (the routing pool can
    only ever be this org's own approvers)."""
    rows = (
        await ctrl_db.execute(
            select(User.id, User.full_name)
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                User.organization_id == organization_id,
                User.is_active.is_(True),
                Role.name.in_(_APPROVAL_ROLES),
            )
            .distinct()
        )
    ).all()
    return {str(uid): name for uid, name in rows}


def _routing_dict(s: RoutingSuggestion) -> dict:
    return {
        "invoice_id": s.invoice_id,
        "vendor_id": s.vendor_id,
        "vendor_name": s.vendor_name,
        "amount": str(s.amount),
        "insufficient_history": s.insufficient_history,
        "candidates": [
            {
                "approver_id": c.approver_id,
                "approver_name": c.approver_name,
                "score": str(c.score),
                "rank": c.rank,
                "median_time_to_approve_days": str(c.median_time_to_approve_days),
                "approval_rate_pct": str(c.approval_rate_pct),
                "sample_size": c.sample_size,
                "vendor_approved_count": c.vendor_approved_count,
                "reasons": c.reasons,
            }
            for c in s.candidates
        ],
    }


@router.get("/routing-suggestion", response_model=RoutingSuggestionResponse)
async def routing_suggestion(
    invoice_id: uuid.UUID = Query(...),
    days: int = Query(180, ge=1, le=730),
    db: AsyncSession = Depends(get_tenant_db),
    ctrl_db: AsyncSession = Depends(get_control_db),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
    user: User = Depends(require_roles(*_READ_ROLES)),
):
    """Advisory: rank the org's eligible approvers by routing fit for this
    invoice (fastest + most-consistent + most-familiar with the vendor), purely
    from their approval history. **Read-only** — never assigns anyone or mutates
    workflow state. The act/apply path is a future slice (see
    backend/docs/adaptive-workflows.md § Smart routing)."""
    q = apply_entity_scope(select(Invoice).where(Invoice.id == invoice_id), Invoice, entity_id)
    inv = (await db.execute(q)).scalar_one_or_none()
    if inv is None:
        raise HTTPException(status_code=404, detail="Invoice not found")

    since = datetime.now(UTC) - timedelta(days=days)
    decisions = await _decision_rows(db, since=since, entity_id=entity_id)

    approvers = compute_approver_patterns(decisions)

    # Per-approver familiarity with THIS vendor: count approvals of this vendor's
    # invoices, keyed by approver id, over the same decision-row window.
    inv_vendor_id = str(inv.vendor_id) if inv.vendor_id else None
    inv_vendor_name = inv.vendor_name or ""
    familiarity: dict[str, int] = {}
    for d in decisions:
        if d["decision"] != "approved" or not d["approver_id"]:
            continue
        same_vendor = (
            d["vendor_id"] == inv_vendor_id
            if inv_vendor_id is not None
            else (d["vendor_id"] is None and d["vendor_name"] == inv_vendor_name)
        )
        if same_vendor:
            familiarity[d["approver_id"]] = familiarity.get(d["approver_id"], 0) + 1

    names = await _eligible_approver_ids(ctrl_db, organization_id=user.organization_id)
    eligible = [
        EligibleApprover(
            approver_id=aid,
            approver_name=name,
            vendor_approved_count=familiarity.get(aid, 0),
        )
        for aid, name in names.items()
    ]

    suggestion = recommend_approvers(
        eligible,
        approvers,
        invoice_id=str(inv.id),
        vendor_id=inv_vendor_id,
        vendor_name=inv_vendor_name,
        amount=Decimal(str(inv.amount or 0)),
    )
    return RoutingSuggestionResponse(**_routing_dict(suggestion))
