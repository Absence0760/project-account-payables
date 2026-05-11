"""CFO + analytics endpoints.

The operational dashboard at `/api/dashboard` covers AP-clerk
metrics (pipeline, aging, recent payments). This module covers the
CFO surface: DPO trend, cash-conversion cycle, accruals, supplier
concentration, fraud-rate trend, rebate yield, forecast variance,
and the drill-through endpoints that let the CFO click through to
the contributing invoice / payment set.

Computation lives in `app/services/analytics.py` (pure functions).
This file does the SQL and the response shaping.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO, require_roles
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.user import User
from app.models.virtual_card import CardRebate
from app.services.analytics import (
    compute_accruals,
    compute_cash_conversion_cycle,
    compute_dpo,
    compute_forecast_variance,
    compute_fraud_rate_trend,
    compute_rebate_yield,
    compute_supplier_concentration,
    compute_working_capital_impact,
)
from app.tenant import get_tenant_db

router = APIRouter(prefix="/analytics", tags=["analytics"])


# Most metrics are read-mostly heavy queries — CFO + admin only.
# AP managers see the operational dashboard but not the CFO surface
# unless the org explicitly grants them.
_CFO_ROLES = (ROLE_ADMIN, ROLE_CFO)


# ---------------------------------------------------------------------------
# /api/analytics/cfo — the aggregate CFO dashboard
# ---------------------------------------------------------------------------


@router.get("/cfo")
async def get_cfo_analytics(
    period_days: int = Query(365, ge=30, le=730),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
):
    """Aggregate every CFO metric into one response so the dashboard
    can render in a single round-trip. Per-metric drill-through is
    still available under `/api/analytics/drill/<metric>`.

    `period_days` defaults to a trailing 365 (annual view); the CFO
    can flip to 90/180 from the UI. Anything outside [30, 730] is
    refused — a 1-day analytics window is noise, a 5-year window
    is a query the operational DB shouldn't be running."""
    today = date.today()
    period_start = today - timedelta(days=period_days)

    # ----- Total spend in window + accounts-payable balance -----
    total_spend_q = await db.execute(
        select(func.coalesce(func.sum(Invoice.amount), 0)).where(
            Invoice.invoice_date >= period_start,
            Invoice.status != InvoiceStatus.rejected.value,
        )
    )
    total_spend = Decimal(str(total_spend_q.scalar() or 0))

    ap_balance_q = await db.execute(
        select(func.coalesce(func.sum(Invoice.amount), 0)).where(
            Invoice.status.in_(
                [
                    InvoiceStatus.approved.value,
                    InvoiceStatus.sending_to_erp.value,
                    InvoiceStatus.sent_to_erp.value,
                    InvoiceStatus.posted_in_erp.value,
                    InvoiceStatus.payment_scheduled.value,
                ]
            )
        )
    )
    ap_balance = Decimal(str(ap_balance_q.scalar() or 0))

    # ----- DPO (using `total_spend` as a COGS proxy when the org -----
    # ----- doesn't surface real COGS data — the dashboard tile -----
    # ----- annotates this as a proxy estimate). -----
    dpo = compute_dpo(accounts_payable=ap_balance, cogs=total_spend, period_days=period_days)

    # ----- DPO trend (last 6 months snapshots) -----
    monthly_dpo_rows = []
    cursor = today.replace(day=1)
    for _ in range(6):
        month_end = cursor - timedelta(days=1)
        month_start = month_end.replace(day=1)
        month_spend_q = await db.execute(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date <= month_end,
            )
        )
        month_ap_q = await db.execute(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.invoice_date <= month_end,
                Invoice.status.in_(
                    [
                        InvoiceStatus.approved.value,
                        InvoiceStatus.sending_to_erp.value,
                        InvoiceStatus.sent_to_erp.value,
                        InvoiceStatus.posted_in_erp.value,
                        InvoiceStatus.payment_scheduled.value,
                    ]
                ),
            )
        )
        cogs_m = Decimal(str(month_spend_q.scalar() or 0))
        ap_m = Decimal(str(month_ap_q.scalar() or 0))
        monthly_dpo_rows.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "dpo": compute_dpo(accounts_payable=ap_m, cogs=cogs_m, period_days=30),
            }
        )
        cursor = month_start
    monthly_dpo_rows.reverse()

    # ----- Cash conversion cycle — DSO/DIO aren't available in -----
    # ----- an AP-only product. We expose None so the UI can render -----
    # ----- "needs receivables data". -----
    ccc = compute_cash_conversion_cycle(dso_days=None, dio_days=None, dpo_days=dpo)

    # ----- Accruals — open POs, GRs, unposted invoices -----
    open_po_q = await db.execute(select(func.coalesce(func.sum(_safe_total_column()), 0)))
    # We use a helper rather than importing PurchaseOrder here to
    # keep the failure surface narrow on tenants that never enabled
    # PO matching — return zero.
    try:
        open_po_amount = Decimal(str(open_po_q.scalar() or 0))
    except Exception:  # noqa: BLE001
        open_po_amount = Decimal("0")

    # Unposted invoices: approved + sending_to_erp + sent_to_erp
    unposted_q = await db.execute(
        select(func.coalesce(func.sum(Invoice.amount), 0)).where(
            Invoice.status.in_(
                [
                    InvoiceStatus.approved.value,
                    InvoiceStatus.sending_to_erp.value,
                    InvoiceStatus.sent_to_erp.value,
                ]
            )
        )
    )
    unposted_invoice_amount = Decimal(str(unposted_q.scalar() or 0))
    # GR-received-but-not-invoiced is a multi-table join — we
    # approximate as 0 today and document on the response; SOC 2
    # auditors flagged this as a known gap. Real number requires
    # the 3-way match layer (po_matching) to fan out per-line.
    accruals = compute_accruals(
        open_po_amount=open_po_amount,
        received_amount=Decimal("0"),
        unposted_invoice_amount=unposted_invoice_amount,
    )

    # ----- Working-capital impact (extend by 5 days) -----
    paid_q = await db.execute(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.completed_at
            >= datetime.combine(period_start, datetime.min.time()).replace(tzinfo=UTC),
            Payment.status == "completed",
        )
    )
    paid_in_period = Decimal(str(paid_q.scalar() or 0))
    days_in_period = max(period_days, 1)
    avg_daily_outflow = (paid_in_period / Decimal(days_in_period)).quantize(Decimal("0.01"))
    wc_impact_5d = compute_working_capital_impact(
        avg_daily_outflow=avg_daily_outflow, days_extended=5
    )

    # ----- Supplier concentration (top-10 / top-50) -----
    vendor_rows = await db.execute(
        select(
            Invoice.vendor_name,
            func.sum(Invoice.amount).label("total"),
        )
        .where(
            Invoice.invoice_date >= period_start,
            Invoice.vendor_name.isnot(None),
            Invoice.vendor_name != "",
        )
        .group_by(Invoice.vendor_name)
        .order_by(func.sum(Invoice.amount).desc())
        .limit(50)
    )
    vendor_spend = [{"vendor": v, "amount": Decimal(str(amt))} for v, amt in vendor_rows.all()]
    concentration = compute_supplier_concentration(vendor_spend)

    # ----- Fraud-rate trend (last 6 months) — proxy: exception -----
    # ----- count / invoice count per month. -----
    fraud_rows: list[dict] = []
    cursor = today.replace(day=1)
    for _ in range(6):
        month_end = cursor - timedelta(days=1)
        month_start = month_end.replace(day=1)
        inv_count_q = await db.execute(
            select(func.count(Invoice.id)).where(
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date <= month_end,
            )
        )
        exc_count = 0
        try:
            from app.models.exception import Exception as APException

            exc_count_q = await db.execute(
                select(func.count(APException.id)).where(
                    APException.created_at
                    >= datetime.combine(month_start, datetime.min.time()).replace(tzinfo=UTC),
                    APException.created_at
                    <= datetime.combine(month_end, datetime.max.time()).replace(tzinfo=UTC),
                )
            )
            exc_count = int(exc_count_q.scalar() or 0)
        except Exception:  # noqa: BLE001
            exc_count = 0
        fraud_rows.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "invoice_count": int(inv_count_q.scalar() or 0),
                "exception_count": exc_count,
                "by_type": {},
            }
        )
        cursor = month_start
    fraud_rows.reverse()
    fraud_trend = compute_fraud_rate_trend(fraud_rows)

    # ----- Rebate yield -----
    try:
        rebate_q = await db.execute(select(func.coalesce(func.sum(CardRebate.amount), 0)))
        rebates_total = Decimal(str(rebate_q.scalar() or 0))
    except Exception:  # noqa: BLE001
        rebates_total = Decimal("0")
    rebate = compute_rebate_yield(
        rebates_total=rebates_total,
        total_spend=total_spend,
        months_in_period=max(period_days // 30, 1),
    )

    return {
        "period_days": period_days,
        "period_start": period_start.isoformat(),
        "total_spend": float(total_spend),
        "accounts_payable_balance": float(ap_balance),
        "dpo_current": float(dpo),
        "dpo_trend": [{"month": r["month"], "dpo": float(r["dpo"])} for r in monthly_dpo_rows],
        "cash_conversion_cycle": float(ccc) if ccc is not None else None,
        "accruals": {
            "open_po_amount": float(accruals.open_po_amount),
            "received_amount": float(accruals.received_amount),
            "unposted_invoice_amount": float(accruals.unposted_invoice_amount),
            "total_accrual": float(accruals.total_accrual),
        },
        "working_capital_impact_5_days": float(wc_impact_5d),
        "avg_daily_outflow": float(avg_daily_outflow),
        "supplier_concentration": {
            "total_spend": float(concentration.total_spend),
            "top_10_share_pct": float(concentration.top_10_share_pct),
            "top_50_share_pct": float(concentration.top_50_share_pct),
            "largest_vendor": concentration.largest_vendor,
            "largest_vendor_share_pct": float(concentration.largest_vendor_share_pct),
            "flagged": concentration.flagged,
        },
        "fraud_rate_trend": [{**r, "rate_pct": float(r["rate_pct"])} for r in fraud_trend],
        "rebate_yield": {
            "rebates_total": float(rebate["rebates_total"]),
            "total_spend": float(rebate["total_spend"]),
            "yield_pct": float(rebate["yield_pct"]),
            "annualised_rebates": float(rebate["annualised_rebates"]),
        },
    }


def _safe_total_column():
    """Lazily resolve PurchaseOrder.total — keeps the import out of
    the module-level so a tenant without procurement tables doesn't
    blow up on import. Returns a `0` literal column if PO model
    isn't reachable."""
    try:
        from app.models.procurement import PurchaseOrder

        return PurchaseOrder.total
    except Exception:  # noqa: BLE001
        from sqlalchemy import literal

        return literal(0)


# ---------------------------------------------------------------------------
# /api/analytics/drill/spend_concentration — top vendors with invoice list
# ---------------------------------------------------------------------------


@router.get("/drill/spend_concentration")
async def drill_spend_concentration(
    period_days: int = Query(365, ge=30, le=730),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
):
    """Drill-through for the supplier-concentration tile. Returns
    the top-N vendors by spend with their share, invoice count,
    and a few representative invoice IDs the CFO can click into."""
    period_start = date.today() - timedelta(days=period_days)
    rows = await db.execute(
        select(
            Invoice.vendor_name,
            func.sum(Invoice.amount).label("total"),
            func.count(Invoice.id).label("invoice_count"),
        )
        .where(
            Invoice.invoice_date >= period_start,
            Invoice.vendor_name.isnot(None),
            Invoice.vendor_name != "",
        )
        .group_by(Invoice.vendor_name)
        .order_by(func.sum(Invoice.amount).desc())
        .limit(limit)
    )
    rows_list = rows.all()
    total = sum((Decimal(str(r[1])) for r in rows_list), Decimal("0"))
    return {
        "period_days": period_days,
        "rows": [
            {
                "vendor": r[0],
                "amount": float(r[1]),
                "share_pct": float(
                    (Decimal(str(r[1])) / total * Decimal("100")).quantize(Decimal("0.1"))
                )
                if total > 0
                else 0.0,
                "invoice_count": int(r[2]),
            }
            for r in rows_list
        ],
        "total_spend": float(total),
    }


# ---------------------------------------------------------------------------
# /api/analytics/drill/dpo — per-month invoice + payment volumes
# ---------------------------------------------------------------------------


@router.get("/drill/dpo")
async def drill_dpo(
    months: int = Query(12, ge=1, le=24),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
):
    """Per-month accounts-payable balance + COGS used to derive
    each DPO point. Lets the CFO see what's driving a spike."""
    today = date.today()
    cursor = today.replace(day=1)
    rows = []
    for _ in range(months):
        month_end = cursor - timedelta(days=1)
        month_start = month_end.replace(day=1)
        cogs_q = await db.execute(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.invoice_date >= month_start,
                Invoice.invoice_date <= month_end,
            )
        )
        ap_q = await db.execute(
            select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                Invoice.invoice_date <= month_end,
                Invoice.status.in_(
                    [
                        InvoiceStatus.approved.value,
                        InvoiceStatus.sending_to_erp.value,
                        InvoiceStatus.sent_to_erp.value,
                        InvoiceStatus.posted_in_erp.value,
                        InvoiceStatus.payment_scheduled.value,
                    ]
                ),
            )
        )
        cogs = Decimal(str(cogs_q.scalar() or 0))
        ap = Decimal(str(ap_q.scalar() or 0))
        dpo_val = compute_dpo(accounts_payable=ap, cogs=cogs, period_days=30)
        rows.append(
            {
                "month": month_start.strftime("%Y-%m"),
                "accounts_payable": float(ap),
                "cogs": float(cogs),
                "dpo": float(dpo_val),
            }
        )
        cursor = month_start
    rows.reverse()
    return {"months": months, "rows": rows}


# ---------------------------------------------------------------------------
# /api/analytics/forecast_variance — bring-your-own forecast
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/analytics/export/{report} — CSV download endpoints
# ---------------------------------------------------------------------------


@router.get("/export/{report}")
async def export_report(
    report: str,
    period_days: int = Query(90, ge=1, le=730),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """CSV download. Supported reports:

      - `invoice_register` — every invoice in the period
      - `vendor_spend` — per-vendor totals
      - `payment_register` — every payment in the period
      - `aging_snapshot` — current/30/60/90+ buckets as-of-today

    Returns `text/csv` with a Content-Disposition header so the
    browser saves it with a sensible filename. AP-manager + CFO
    can both pull these — they're operational reports, not
    privileged CFO analytics.
    """
    from app.services.report_export import EXPORTERS

    if report not in EXPORTERS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown report '{report}'; supported: {sorted(EXPORTERS)}",
        )

    period_start = date.today() - timedelta(days=period_days)
    payload: str

    if report == "invoice_register":
        rows = await db.execute(select(Invoice).where(Invoice.invoice_date >= period_start))
        payload = EXPORTERS[report](rows.scalars().all())
    elif report == "vendor_spend":
        rows = await db.execute(
            select(
                Invoice.vendor_name,
                func.count(Invoice.id).label("invoice_count"),
                func.coalesce(func.sum(Invoice.amount), 0).label("total"),
            )
            .where(
                Invoice.invoice_date >= period_start,
                Invoice.vendor_name.isnot(None),
                Invoice.vendor_name != "",
            )
            .group_by(Invoice.vendor_name)
            .order_by(func.sum(Invoice.amount).desc())
        )
        payload = EXPORTERS[report](rows.all())
    elif report == "payment_register":
        rows = await db.execute(
            select(Payment, Invoice)
            .outerjoin(Invoice, Invoice.id == Payment.invoice_id)
            .where(
                Payment.created_at
                >= datetime.combine(period_start, datetime.min.time()).replace(tzinfo=UTC)
            )
        )
        payload = EXPORTERS[report](rows.all())
    else:  # aging_snapshot
        today = date.today()
        open_statuses = (
            "new",
            "pending",
            "ready_for_review",
            "approved",
        )
        aging_rows = await db.execute(
            select(Invoice.due_date, Invoice.amount).where(
                Invoice.status.in_(open_statuses),
                Invoice.due_date.isnot(None),
            )
        )
        buckets = {
            "current": Decimal("0"),
            "days_30": Decimal("0"),
            "days_60": Decimal("0"),
            "days_90_plus": Decimal("0"),
        }
        for due, amt in aging_rows.all():
            days_past = (today - due).days
            amount = Decimal(str(amt))
            if days_past <= 0:
                buckets["current"] += amount
            elif days_past <= 30:
                buckets["days_30"] += amount
            elif days_past <= 60:
                buckets["days_60"] += amount
            else:
                buckets["days_90_plus"] += amount
        payload = EXPORTERS[report](buckets)

    filename = f"{report}_{date.today().isoformat()}.csv"
    return Response(
        content=payload,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/forecast_variance")
async def post_forecast_variance(
    body: dict,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_CFO_ROLES)),
):
    """The org POSTs `{"months": [{"month": "YYYY-MM",
    "forecast": "100000"}, ...]}` and we return the same list with
    the actual outflow per month + the variance + variance_pct.

    Forecasts are not persisted — the CFO either pastes from their
    FP&A tool or re-runs the call with adjusted numbers. The
    actuals-vs-forecast comparison is what we contribute."""
    rows = body.get("months") or []
    if not isinstance(rows, list):
        raise HTTPException(status_code=400, detail="`months` must be a list")
    augmented: list[dict] = []
    for r in rows:
        month = r.get("month")
        if not isinstance(month, str) or len(month) != 7:
            raise HTTPException(status_code=400, detail="each row needs `month` in YYYY-MM format")
        try:
            year, mon = (int(x) for x in month.split("-"))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"bad month value: {month!r}") from exc
        start = date(year, mon, 1)
        end = date(year + (mon // 12), (mon % 12) + 1, 1) - timedelta(days=1)
        actual_q = await db.execute(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "completed",
                Payment.completed_at
                >= datetime.combine(start, datetime.min.time()).replace(tzinfo=UTC),
                Payment.completed_at
                <= datetime.combine(end, datetime.max.time()).replace(tzinfo=UTC),
            )
        )
        augmented.append(
            {
                "month": month,
                "forecast": r.get("forecast", "0"),
                "actual": str(actual_q.scalar() or 0),
            }
        )
    result = compute_forecast_variance(augmented)
    return {
        "rows": [
            {
                "month": r["month"],
                "forecast": float(r["forecast"]),
                "actual": float(r["actual"]),
                "variance": float(r["variance"]),
                "variance_pct": float(r["variance_pct"]),
            }
            for r in result
        ]
    }
