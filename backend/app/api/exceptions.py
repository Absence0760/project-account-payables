"""Exception queue endpoints — view and resolve flagged invoices."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
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


@router.get("")
async def list_exceptions(
    status_filter: str | None = Query(None, alias="status"),
    exception_type: str | None = Query(None, alias="type"),
    severity: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    query = select(APException, Invoice).outerjoin(Invoice, APException.invoice_id == Invoice.id)

    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(APException.status.in_(statuses))
    if exception_type:
        query = query.where(APException.exception_type == exception_type)
    if severity:
        query = query.where(APException.severity == severity)

    query = query.order_by(APException.created_at.desc())
    result = await db.execute(query)
    rows = result.all()

    return {
        "items": [
            {
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
                "created_at": exc.created_at.isoformat() if exc.created_at else "",
            }
            for exc, inv in rows
        ],
        "total": len(rows),
    }


@router.get("/summary")
async def exception_summary(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
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


@router.post("/{exception_id}/resolve")
async def resolve_exception(
    exception_id: uuid.UUID,
    body: ResolveRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    result = await db.execute(select(APException).where(APException.id == exception_id))
    exc = result.scalar_one_or_none()
    if not exc:
        raise HTTPException(status_code=404, detail="Exception not found")

    if exc.status not in ("open", "escalated"):
        raise HTTPException(status_code=409, detail=f"Cannot resolve from '{exc.status}' status")

    if body.action == "resolve":
        exc.status = "resolved"
    elif body.action == "escalate":
        exc.status = "escalated"
    elif body.action == "dismiss":
        exc.status = "dismissed"
    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    exc.resolution = body.resolution
    exc.resolved_by = user.full_name
    exc.resolved_at = datetime.now(UTC)
    await db.commit()

    return {"id": str(exc.id), "status": exc.status, "message": f"Exception {body.action}d"}
