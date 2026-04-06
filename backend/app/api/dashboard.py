"""Dashboard aggregation endpoints — rich KPIs for the main page."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.user import User
from app.models.virtual_card import CardRebate
from app.tenant import get_tenant_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    today = date.today()

    # KPIs
    totals = await db.execute(
        select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.amount), 0))
    )
    total_invoices, total_amount = totals.one()

    # Pipeline (count per status)
    status_rows = await db.execute(
        select(Invoice.status, func.count(Invoice.id)).group_by(Invoice.status)
    )
    pipeline = {
        str(row[0].value if hasattr(row[0], "value") else row[0]): row[1]
        for row in status_rows.all()
    }

    # Spend by vendor (top 10)
    vendor_spend_rows = await db.execute(
        select(Invoice.vendor_name, func.sum(Invoice.amount).label("total"))
        .where(Invoice.vendor_name.isnot(None), Invoice.vendor_name != "")
        .group_by(Invoice.vendor_name)
        .order_by(func.sum(Invoice.amount).desc())
        .limit(10)
    )
    vendor_spend = [
        {"vendor": row[0], "amount": float(row[1])} for row in vendor_spend_rows.all()
    ]

    # Aging buckets
    aging = {"current": 0.0, "days_30": 0.0, "days_60": 0.0, "days_90_plus": 0.0}
    open_statuses = ("new", "pending", "ready_for_review", "approved")
    aging_rows = await db.execute(
        select(Invoice.due_date, Invoice.amount).where(
            Invoice.status.in_(open_statuses), Invoice.due_date.isnot(None)
        )
    )
    for row in aging_rows.all():
        days_past = (today - row[0]).days
        amt = float(row[1])
        if days_past <= 0:
            aging["current"] += amt
        elif days_past <= 30:
            aging["days_30"] += amt
        elif days_past <= 60:
            aging["days_60"] += amt
        else:
            aging["days_90_plus"] += amt

    # Monthly trend (last 6 months) — compute in Python to avoid GROUP BY issues
    six_months_ago = today - timedelta(days=180)
    trend_inv_rows = await db.execute(
        select(Invoice.invoice_date, Invoice.amount)
        .where(Invoice.invoice_date >= six_months_ago, Invoice.invoice_date.isnot(None))
    )
    trend_buckets: dict[str, dict] = {}
    for row in trend_inv_rows.all():
        month_key = row[0].strftime("%Y-%m")
        if month_key not in trend_buckets:
            trend_buckets[month_key] = {"month": month_key, "count": 0, "amount": 0.0}
        trend_buckets[month_key]["count"] += 1
        trend_buckets[month_key]["amount"] += float(row[1])
    monthly_trend = sorted(trend_buckets.values(), key=lambda x: x["month"])

    # Upcoming payments (due within 7 days + overdue)
    week_ahead = today + timedelta(days=7)
    paid_ids = (
        select(Payment.invoice_id).where(Payment.status == "completed").scalar_subquery()
    )
    upcoming_rows = await db.execute(
        select(
            Invoice.id, Invoice.invoice_number, Invoice.vendor_name,
            Invoice.amount, Invoice.due_date,
        )
        .where(
            Invoice.due_date.isnot(None),
            Invoice.due_date <= week_ahead,
            Invoice.id.notin_(paid_ids),
        )
        .order_by(Invoice.due_date)
        .limit(10)
    )
    upcoming = [
        {
            "id": str(r[0]),
            "invoice_number": r[1],
            "vendor_name": r[2],
            "amount": float(r[3]),
            "due_date": r[4].isoformat() if r[4] else None,
            "is_overdue": r[4] < today if r[4] else False,
        }
        for r in upcoming_rows.all()
    ]

    # Touchless rate
    terminal = ("approved", "sent_to_erp", "posted_in_erp", "payment_scheduled", "paid", "done")
    total_processed = sum(pipeline.get(s, 0) for s in terminal)
    rejected_count = pipeline.get("rejected", 0)
    touchless_rate = round(
        ((total_processed - rejected_count) / total_processed * 100)
        if total_processed > 0 else 0, 1,
    )

    # Payment totals — separate queries to avoid complex CASE expressions
    paid_q = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.status == "completed")
    )
    total_paid = float(paid_q.scalar() or 0)

    pending_q = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0))
        .where(Payment.status.in_(["pending", "processing"]))
    )
    total_pending = float(pending_q.scalar() or 0)

    # Rebates
    try:
        rebate_q = await db.execute(select(func.coalesce(func.sum(CardRebate.amount), 0)))
        total_rebates = float(rebate_q.scalar() or 0)
    except Exception:
        total_rebates = 0.0
        await db.rollback()

    # Stale approvals (waiting > 3 days)
    stale_date = today - timedelta(days=3)
    stale_q = await db.execute(
        select(func.count()).where(
            Invoice.status == "ready_for_review",
            Invoice.created_at <= stale_date,
        )
    )
    stale_approvals = stale_q.scalar() or 0

    return {
        "total_invoices": total_invoices or 0,
        "total_amount": float(total_amount),
        "total_paid": total_paid,
        "total_pending": total_pending,
        "total_rebates": total_rebates,
        "touchless_rate": touchless_rate,
        "stale_approvals": stale_approvals,
        "pipeline": pipeline,
        "vendor_spend": vendor_spend,
        "aging": aging,
        "monthly_trend": monthly_trend,
        "upcoming_payments": upcoming,
    }
