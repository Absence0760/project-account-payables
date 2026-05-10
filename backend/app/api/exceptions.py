"""Exception queue endpoints — view, assign, and resolve flagged invoices."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, ROLE_AP_MANAGER, require_roles
from app.database import get_control_db
from app.models.exception import Exception as APException
from app.models.invoice import Invoice
from app.models.user import User
from app.tenant import get_tenant_db

router = APIRouter(prefix="/exceptions", tags=["exceptions"])

EXCEPTION_TYPE_LABELS = {
    "duplicate": "Duplicate Invoice",
    "po_mismatch": "PO Mismatch",
    "fraud_flag": "Fraud Flag",
    "extraction_failed": "Extraction Failed",
    "unverified_vendor": "Unverified Vendor",
    "review_rejected": "Rejected",
    "amount_exceeded": "Amount Exceeded",
    "missing_data": "Missing Data",
}


def _exception_dict(exc: APException, inv: Invoice | None) -> dict:
    """Serialise an exception row + its invoice into the JSON shape
    the queue UI consumes. Centralised so the list, assign, and bulk
    handlers all return the same shape."""
    now = datetime.now(UTC)
    is_overdue = bool(
        exc.due_at and exc.status in ("open", "escalated") and exc.due_at < now
    )
    time_to_resolution_hours = (
        round(exc.time_to_resolution_seconds / 3600, 2)
        if exc.time_to_resolution_seconds is not None
        else None
    )
    return {
        "id": str(exc.id),
        "invoice_id": str(exc.invoice_id),
        "invoice_number": inv.invoice_number if inv else None,
        "vendor_name": inv.vendor_name if inv else None,
        "amount": float(inv.amount) if inv else None,
        "exception_type": exc.exception_type,
        "type_label": EXCEPTION_TYPE_LABELS.get(exc.exception_type, exc.exception_type),
        "severity": exc.severity,
        "description": exc.description,
        "status": exc.status,
        "resolution": exc.resolution,
        "resolved_by": exc.resolved_by,
        "resolved_at": exc.resolved_at.isoformat() if exc.resolved_at else None,
        "assigned_to": exc.assigned_to,
        "assigned_to_user_id": str(exc.assigned_to_user_id) if exc.assigned_to_user_id else None,
        "due_at": exc.due_at.isoformat() if exc.due_at else None,
        "is_overdue": is_overdue,
        "time_to_resolution_hours": time_to_resolution_hours,
        "created_at": exc.created_at.isoformat() if exc.created_at else "",
    }


@router.get("")
async def list_exceptions(
    status_filter: str | None = Query(None, alias="status"),
    exception_type: str | None = Query(None, alias="type"),
    severity: str | None = None,
    assigned_to_user_id: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    query = select(APException, Invoice).outerjoin(Invoice, APException.invoice_id == Invoice.id)

    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(APException.status.in_(statuses))
    if exception_type:
        query = query.where(APException.exception_type == exception_type)
    if severity:
        query = query.where(APException.severity == severity)
    if assigned_to_user_id:
        try:
            uid = uuid.UUID(assigned_to_user_id)
        except ValueError as exc_:
            raise HTTPException(status_code=400, detail="Invalid assigned_to_user_id") from exc_
        query = query.where(APException.assigned_to_user_id == uid)

    query = query.order_by(APException.created_at.desc())
    result = await db.execute(query)
    rows = result.all()

    return {
        "items": [_exception_dict(exc, inv) for exc, inv in rows],
        "total": len(rows),
    }


@router.get("/summary")
async def exception_summary(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Counts by status and type for the exception queue."""
    # By status
    status_rows = await db.execute(
        select(APException.status, func.count(APException.id)).group_by(APException.status)
    )
    by_status = {row[0]: row[1] for row in status_rows.all()}

    # By type (open only)
    type_rows = await db.execute(
        select(APException.exception_type, func.count(APException.id))
        .where(APException.status == "open")
        .group_by(APException.exception_type)
    )
    by_type = {row[0]: row[1] for row in type_rows.all()}

    return {
        "open": by_status.get("open", 0),
        "escalated": by_status.get("escalated", 0),
        "resolved": by_status.get("resolved", 0),
        "dismissed": by_status.get("dismissed", 0),
        "by_type": by_type,
    }


class ResolveRequest(BaseModel):
    resolution: str
    action: str = "resolve"  # resolve, escalate, dismiss


def _apply_resolution(exc: APException, action: str, resolution: str, actor_name: str) -> None:
    """Mutate the exception for `action`. Computes time-to-resolution
    when it lands in a terminal state. Caller commits the session."""
    if action == "resolve":
        exc.status = "resolved"
    elif action == "escalate":
        exc.status = "escalated"
    elif action == "dismiss":
        exc.status = "dismissed"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    now = datetime.now(UTC)
    exc.resolution = resolution
    exc.resolved_by = actor_name
    exc.resolved_at = now
    if action in ("resolve", "dismiss") and exc.created_at is not None:
        # Compute SLA observance once, on the trip to a terminal
        # state. `escalated` is non-terminal — leaves the field blank
        # until a follow-up resolve/dismiss lands.
        delta = now - exc.created_at
        exc.time_to_resolution_seconds = int(delta.total_seconds())


# ---------- Bulk resolve --------------------------------------------------
# Registered BEFORE the parameterised `/{exception_id}/resolve` so the
# literal `/bulk/resolve` path doesn't get matched as exception_id="bulk"
# (FastAPI routes match in declaration order).


class BulkResolveRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1)
    action: str = "resolve"  # resolve, escalate, dismiss
    resolution: str


class BulkResolveResponse(BaseModel):
    updated: int
    skipped: list[dict] = Field(default_factory=list)


@router.post("/bulk/resolve", response_model=BulkResolveResponse)
async def bulk_resolve(
    body: BulkResolveRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Resolve / escalate / dismiss many exceptions at once. Common
    pattern: bulk-dismiss the duplicate-detection backlog after a
    semantic-dedup tuning pass.

    Per-row failures (already resolved, unknown id) come back in
    `skipped` with a reason — same partial-success contract as the
    invoice bulk endpoints."""
    if body.action not in ("resolve", "escalate", "dismiss"):
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    try:
        ids = [uuid.UUID(i) for i in body.ids]
    except ValueError as exc_:
        raise HTTPException(status_code=400, detail=f"Invalid id: {exc_}") from exc_

    rows = (
        await db.execute(select(APException).where(APException.id.in_(ids)))
    ).scalars().all()
    seen_ids = {row.id for row in rows}

    updated = 0
    skipped: list[dict] = []
    for missing in ids:
        if missing not in seen_ids:
            skipped.append({"id": str(missing), "reason": "not_found"})

    for exc in rows:
        if exc.status not in ("open", "escalated"):
            skipped.append({"id": str(exc.id), "reason": f"already_{exc.status}"})
            continue
        _apply_resolution(exc, body.action, body.resolution, user.full_name)
        updated += 1

    await db.commit()
    return BulkResolveResponse(updated=updated, skipped=skipped)


@router.post("/{exception_id}/resolve")
async def resolve_exception(
    exception_id: uuid.UUID,
    body: ResolveRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    result = await db.execute(select(APException).where(APException.id == exception_id))
    exc = result.scalar_one_or_none()
    if not exc:
        raise HTTPException(status_code=404, detail="Exception not found")

    if exc.status not in ("open", "escalated"):
        raise HTTPException(status_code=409, detail=f"Cannot resolve from '{exc.status}' status")

    _apply_resolution(exc, body.action, body.resolution, user.full_name)
    await db.commit()

    return {"id": str(exc.id), "status": exc.status, "message": f"Exception {body.action}d"}


# ---------- Assignment ----------------------------------------------------


class AssignRequest(BaseModel):
    """Assign an exception to a user (or unassign by passing null)."""

    user_id: str | None = Field(default=None, description="User UUID, or null to unassign")


@router.post("/{exception_id}/assign")
async def assign_exception(
    exception_id: uuid.UUID,
    body: AssignRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctrl_db: AsyncSession = Depends(get_control_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Assign (or unassign by passing user_id=null) an open exception
    to a specific user. The user must belong to the same organization."""
    result = await db.execute(select(APException).where(APException.id == exception_id))
    exc = result.scalar_one_or_none()
    if not exc:
        raise HTTPException(status_code=404, detail="Exception not found")
    if exc.status not in ("open", "escalated"):
        raise HTTPException(status_code=409, detail=f"Cannot assign from '{exc.status}' status")

    if body.user_id:
        try:
            target_uuid = uuid.UUID(body.user_id)
        except ValueError as exc_:
            raise HTTPException(status_code=400, detail="Invalid user_id") from exc_
        # Lookup in the control plane: user must be in this org.
        u = (
            await ctrl_db.execute(
                select(User).where(User.id == target_uuid, User.organization_id == user.organization_id)
            )
        ).scalar_one_or_none()
        if u is None:
            raise HTTPException(status_code=404, detail="User not found in this organization")
        exc.assigned_to_user_id = u.id
        exc.assigned_to = u.full_name
    else:
        exc.assigned_to_user_id = None
        exc.assigned_to = None

    await db.commit()

    inv_result = await db.execute(select(Invoice).where(Invoice.id == exc.invoice_id))
    inv = inv_result.scalar_one_or_none()
    return _exception_dict(exc, inv)
