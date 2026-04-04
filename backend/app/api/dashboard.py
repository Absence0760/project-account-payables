"""Dashboard aggregation endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tenant import get_tenant_db
from app.models.invoice import Invoice
from app.schemas.dashboard import DashboardResponse, StatusCount

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def get_dashboard(
    db: AsyncSession = Depends(get_tenant_db),
):
    # Total invoices and amount
    totals = await db.execute(
        select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.amount), 0))
    )
    total_invoices, total_amount = totals.one()

    # Counts by status
    status_rows = await db.execute(
        select(Invoice.status, func.count(Invoice.id)).group_by(Invoice.status)
    )

    status_counts = [
        StatusCount(status=row[0], count=row[1]) for row in status_rows.all()
    ]

    return DashboardResponse(
        total_invoices=total_invoices,
        total_amount=float(total_amount),
        status_counts=status_counts,
    )
