"""Dashboard aggregation endpoints — rich KPIs for the main page."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.models.exception import Exception as APException
from app.models.invoice import Invoice
from app.models.organization import Organization
from app.models.payment import Payment
from app.models.user import User
from app.models.virtual_card import CardRebate
from app.services.analytics import OPEN_AP_STATUSES
from app.services.currency_conversion import (
    resolve_reporting_currency,
    rollup_from_grouped_rows,
)
from app.tenant import apply_entity_scope, get_entity_id, get_tenant, get_tenant_db

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# Lightweight row shapes the analytics service expects. Defined at
# module level so they're cheap to instantiate inside the endpoint
# and trivial to mock in tests.
@dataclass
class SimpleNamespaceTimings:
    id: object
    created_at: datetime | None
    approved_at: datetime | None
    paid_at: datetime | None


@dataclass
class SimpleNamespaceStep:
    assigned_to: object | None
    created_at: datetime | None
    assignee_name: str | None


@dataclass
class SimpleNamespaceDiscount:
    discount_eligible: bool
    discount_amount: Decimal
    paid_before_discount_date: bool


@router.get("")
async def get_dashboard(
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    today = date.today()

    # Every Invoice/Payment/Exception query below is entity-scoped via
    # `_inv` / `_pay` / `_exc` helpers (None = consolidated). Metrics keyed by
    # invoice id (processing time) inherit the scope from the invoice query they
    # consume; WorkflowStep- and AuditLog-derived inputs have no entity column
    # and stay org-wide until the workflow engine is entity-aware (Phase 3).
    def _inv(q):
        return apply_entity_scope(q, Invoice, entity_id)

    def _pay(q):
        return apply_entity_scope(q, Payment, entity_id)

    def _exc(q):
        return apply_entity_scope(q, APException, entity_id)

    # KPIs
    totals = await db.execute(
        _inv(select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.amount), 0)))
    )
    total_invoices, total_amount = totals.one()

    # ----- Multi-currency reporting rollup --------------------------
    # `total_amount` above is a naive SUM that mixes currencies — fine when
    # every invoice is in one currency, wrong the moment an org books a EUR
    # invoice alongside USD ones. Re-roll the whole book into the org's
    # reporting currency using each row's rate-locked `reporting_amount`
    # (foreign rows without a lock fall back to face value and are counted in
    # `unconverted_count`). See backend/docs/multi-currency.md.
    reporting_currency = resolve_reporting_currency(org.settings)
    # Aggregate per currency in SQL instead of streaming every invoice into
    # Python. The CASE expressions mirror `reporting_amount_for_row`: a row is
    # "locked" (use its persisted `reporting_amount`) iff that column is set AND
    # its `reporting_currency` equals the org's target; otherwise it falls back
    # to face `amount`, and a foreign row (currency != target) is counted as
    # unconverted. `rollup_from_grouped_rows` reduces these per-currency sums to
    # the exact same `ReportingRollup` the row-at-a-time path produced.
    tgt = reporting_currency.upper()
    _cur_key = func.upper(func.coalesce(Invoice.currency, tgt))
    _has_lock = and_(
        Invoice.reporting_amount.isnot(None),
        func.upper(Invoice.reporting_currency) == tgt,
    )
    _rep_expr = case((_has_lock, Invoice.reporting_amount), else_=Invoice.amount)
    _unconv_expr = case((and_(not_(_has_lock), _cur_key != tgt), 1), else_=0)
    rollup_rows = await db.execute(
        _inv(
            select(
                _cur_key.label("currency"),
                func.coalesce(func.sum(Invoice.amount), 0),
                func.coalesce(func.sum(_rep_expr), 0),
                func.count(),
                func.coalesce(func.sum(_unconv_expr), 0),
            ).group_by(_cur_key)
        )
    )
    rollup = rollup_from_grouped_rows(
        [
            {
                "currency": r[0],
                "original_amount": r[1],
                "reporting_amount": r[2],
                "count": r[3],
                "unconverted_count": r[4],
            }
            for r in rollup_rows.all()
        ],
        reporting_currency=reporting_currency,
    )

    # Pipeline (count per status)
    status_rows = await db.execute(
        _inv(select(Invoice.status, func.count(Invoice.id)).group_by(Invoice.status))
    )
    pipeline = {
        str(row[0].value if hasattr(row[0], "value") else row[0]): row[1]
        for row in status_rows.all()
    }

    # Spend by vendor (top 10) — exclude rejected invoices (they were never
    # real spend), matching the CFO analytics concentration figure.
    vendor_spend_rows = await db.execute(
        _inv(
            select(Invoice.vendor_name, func.sum(Invoice.amount).label("total"))
            .where(
                Invoice.vendor_name.isnot(None),
                Invoice.vendor_name != "",
                Invoice.status != "rejected",
            )
            .group_by(Invoice.vendor_name)
            .order_by(func.sum(Invoice.amount).desc())
            .limit(10)
        )
    )
    vendor_spend = [{"vendor": row[0], "amount": float(row[1])} for row in vendor_spend_rows.all()]

    # Aging buckets — boundaries are days past the due date:
    # current (not yet due) / 1-30 / 31-60 / 61-90 / 90+.
    # Accumulate in Decimal (money is never summed in float — many small floats
    # drift), then convert to float at the response boundary.
    aging_dec = {
        "current": Decimal("0"),
        "days_30": Decimal("0"),
        "days_60": Decimal("0"),
        "days_90": Decimal("0"),
        "days_90_plus": Decimal("0"),
    }
    # Aging covers the same open-payable population as the AP balance so the
    # bands reconcile with it (F-4): approved → payment_scheduled. The AP
    # balance has no due_date filter, so aging must not either — an open
    # invoice missing a due date used to inflate the balance while vanishing
    # from every bucket. A null due_date can't be judged overdue, so it
    # buckets as "current" (the conservative read) rather than being dropped.
    # Bucket + sum in SQL (Postgres `date - date` is integer days) rather than
    # pulling every open row into Python; the band boundaries match the old
    # loop exactly.
    _days_past = today - Invoice.due_date
    _aging_bucket = case(
        (Invoice.due_date.is_(None), "current"),
        (_days_past <= 0, "current"),
        (_days_past <= 30, "days_30"),
        (_days_past <= 60, "days_60"),
        (_days_past <= 90, "days_90"),
        else_="days_90_plus",
    )
    aging_rows = await db.execute(
        _inv(
            select(_aging_bucket.label("bucket"), func.coalesce(func.sum(Invoice.amount), 0))
            .where(Invoice.status.in_(OPEN_AP_STATUSES))
            .group_by(_aging_bucket)
        )
    )
    for bucket, total in aging_rows.all():
        aging_dec[bucket] = Decimal(str(total))
    aging = {k: float(v) for k, v in aging_dec.items()}

    # Monthly trend (last 6 months) — bucket by calendar month in SQL rather
    # than streaming every recent invoice into Python. Summing amounts in the
    # DB (Numeric) also avoids the float-accumulation drift the old per-row
    # `+= float(...)` fold could introduce.
    six_months_ago = today - timedelta(days=180)
    _month_key = func.to_char(func.date_trunc("month", Invoice.invoice_date), "YYYY-MM")
    trend_rows = await db.execute(
        _inv(
            select(
                _month_key.label("month"), func.count(), func.coalesce(func.sum(Invoice.amount), 0)
            )
            .where(Invoice.invoice_date >= six_months_ago, Invoice.invoice_date.isnot(None))
            .group_by(_month_key)
            .order_by(_month_key)
        )
    )
    monthly_trend = [
        {"month": month, "count": count, "amount": float(amount)}
        for month, count, amount in trend_rows.all()
    ]

    # Upcoming payments (due within 7 days + overdue)
    week_ahead = today + timedelta(days=7)
    paid_ids = select(Payment.invoice_id).where(Payment.status == "completed").scalar_subquery()
    upcoming_rows = await db.execute(
        _inv(
            select(
                Invoice.id,
                Invoice.invoice_number,
                Invoice.vendor_name,
                Invoice.amount,
                Invoice.due_date,
            )
            .where(
                Invoice.due_date.isnot(None),
                Invoice.due_date <= week_ahead,
                Invoice.id.notin_(paid_ids),
            )
            .order_by(Invoice.due_date)
            .limit(10)
        )
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

    # Touchless rate — share of invoices that cleared review straight through
    # (reached approved-or-beyond) out of every invoice that has finished the
    # review stage (the same approved-or-beyond states PLUS the ones bounced to
    # `rejected`). Numerator is a strict subset of the denominator, so the rate
    # is always in [0, 100] — it can never go negative.
    auto_processed_statuses = (
        "approved",
        "sent_to_erp",
        "posted_in_erp",
        "payment_scheduled",
        "paid",
        "done",
    )
    auto_processed = sum(pipeline.get(s, 0) for s in auto_processed_statuses)
    rejected_count = pipeline.get("rejected", 0)
    reviewed_total = auto_processed + rejected_count
    touchless_rate = round(
        (auto_processed / reviewed_total * 100) if reviewed_total > 0 else 0,
        1,
    )

    # Payment totals — separate queries to avoid complex CASE expressions
    paid_q = await db.execute(
        _pay(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "completed")
        )
    )
    total_paid = float(paid_q.scalar() or 0)

    pending_q = await db.execute(
        _pay(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status.in_(["pending", "processing"])
            )
        )
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
        _inv(
            select(func.count()).where(
                Invoice.status == "ready_for_review",
                Invoice.created_at <= stale_date,
            )
        )
    )
    stale_approvals = stale_q.scalar() or 0

    # Open exceptions
    try:
        exc_q = await db.execute(
            _exc(select(func.count()).where(APException.status.in_(["open", "escalated"])))
        )
        open_exceptions = exc_q.scalar() or 0
    except Exception:
        open_exceptions = 0
        await db.rollback()

    # ----- Processing-time metrics ----------------------------------
    # Avg + median + p95 from invoice creation to (a) approval (read
    # from the audit_log row stamped on `invoice.approved`) and (b)
    # completed payment (read from Payment.completed_at). Both legs
    # collapse to 0 when the sample is below the min threshold (see
    # `services/analytics.compute_processing_time_metrics`).
    from app.models.workflow import AuditLog
    from app.services.analytics import compute_processing_time_metrics

    invoice_legs: list = []
    try:
        approval_rows = await db.execute(
            select(AuditLog.entity_id, AuditLog.created_at).where(
                AuditLog.entity_type == "invoice",
                AuditLog.action == "invoice.approved",
            )
        )
        approved_at_by_invoice = {row[0]: row[1] for row in approval_rows.all()}
        paid_rows = await db.execute(
            select(Payment.invoice_id, func.min(Payment.completed_at))
            .where(Payment.status == "completed")
            .group_by(Payment.invoice_id)
        )
        paid_at_by_invoice = {row[0]: row[1] for row in paid_rows.all() if row[1]}
        # Only invoices with an approval or paid timestamp contribute a leg —
        # every other row appends nothing. So fetch just those ids instead of
        # the whole (potentially multi-million-row) invoice table; the entity
        # scope still applies, so an out-of-scope id is dropped exactly as before.
        relevant_ids = set(approved_at_by_invoice) | set(paid_at_by_invoice)
        if relevant_ids:
            inv_rows = await db.execute(
                _inv(select(Invoice.id, Invoice.created_at).where(Invoice.id.in_(relevant_ids)))
            )
            for inv_id, created in inv_rows.all():
                invoice_legs.append(
                    SimpleNamespaceTimings(
                        id=inv_id,
                        created_at=created,
                        approved_at=approved_at_by_invoice.get(inv_id),
                        paid_at=paid_at_by_invoice.get(inv_id),
                    )
                )
    except Exception:  # noqa: BLE001
        # Tenants without the audit_log shipping pipeline enabled can
        # still see the rest of the dashboard. Surface zeros and a
        # note in the response.
        await db.rollback()
        invoice_legs = []

    pt = compute_processing_time_metrics(invoice_legs)

    # ----- Approval bottleneck --------------------------------------
    # Per-approver pending counts + oldest age + average age. Reads
    # from WorkflowStep rows where step_type='approval' and
    # completed_at IS NULL.
    from app.models.workflow import WorkflowStep
    from app.services.analytics import compute_approval_bottleneck

    try:
        step_rows = await db.execute(
            select(
                WorkflowStep.assigned_to,
                WorkflowStep.created_at,
            ).where(
                WorkflowStep.step_type == "approval",
                WorkflowStep.completed_at.is_(None),
            )
        )
        pending_steps = [
            SimpleNamespaceStep(
                assigned_to=row[0],
                created_at=row[1],
                assignee_name=None,
            )
            for row in step_rows.all()
        ]
    except Exception:  # noqa: BLE001
        await db.rollback()
        pending_steps = []
    bottleneck_rows = compute_approval_bottleneck(pending_steps)

    # ----- Discount capture rate ------------------------------------
    # Join Invoice → PaymentSchedule → Payment(completed). Eligible
    # iff `discount_percent` is set; captured iff paid before
    # discount_date. The fold itself is in analytics so the dashboard
    # response stays JSON-thin.
    from app.models.payment import PaymentSchedule
    from app.services.analytics import compute_discount_capture

    try:
        sched_rows = await db.execute(
            _inv(
                select(
                    Invoice.amount,
                    PaymentSchedule.discount_percent,
                    PaymentSchedule.discount_date,
                    func.min(Payment.completed_at).label("paid_at"),
                )
                .join(PaymentSchedule, PaymentSchedule.invoice_id == Invoice.id)
                .outerjoin(
                    Payment,
                    (Payment.invoice_id == Invoice.id) & (Payment.status == "completed"),
                )
                .where(PaymentSchedule.discount_percent.isnot(None))
                .group_by(
                    Invoice.id,
                    Invoice.amount,
                    PaymentSchedule.discount_percent,
                    PaymentSchedule.discount_date,
                )
            )
        )
        discount_input = []
        for amount, pct, ddate, paid_at in sched_rows.all():
            discount_amt = (Decimal(str(amount)) * Decimal(str(pct)) / Decimal("100")).quantize(
                Decimal("0.01")
            )
            paid_before = bool(
                paid_at is not None and ddate is not None and paid_at.date() <= ddate
            )
            discount_input.append(
                SimpleNamespaceDiscount(
                    discount_eligible=True,
                    discount_amount=discount_amt,
                    paid_before_discount_date=paid_before,
                )
            )
    except Exception:  # noqa: BLE001
        await db.rollback()
        discount_input = []
    discount = compute_discount_capture(discount_input)

    return {
        "total_invoices": total_invoices or 0,
        "total_amount": float(total_amount),
        # Currency-aware rollup of the whole invoice book into ONE reporting
        # currency, plus the per-currency split so the UI can show the mix.
        "reporting": {
            "reporting_currency": rollup.reporting_currency,
            "total_amount": float(rollup.total_reporting_amount),
            "total_count": rollup.total_count,
            "unconverted_count": rollup.unconverted_count,
            "by_currency": [
                {
                    "currency": e.currency,
                    "original_amount": float(e.original_amount),
                    "reporting_amount": float(e.reporting_amount),
                    "count": e.count,
                    "unconverted_count": e.unconverted_count,
                }
                for e in rollup.by_currency
            ],
        },
        "total_paid": total_paid,
        "total_pending": total_pending,
        "total_rebates": total_rebates,
        "open_exceptions": open_exceptions,
        "touchless_rate": touchless_rate,
        "stale_approvals": stale_approvals,
        "pipeline": pipeline,
        "vendor_spend": vendor_spend,
        "aging": aging,
        "monthly_trend": monthly_trend,
        "upcoming_payments": upcoming,
        "processing_time": {
            "avg_upload_to_approval_days": float(pt.avg_upload_to_approval_days),
            "median_upload_to_approval_days": float(pt.median_upload_to_approval_days),
            "p95_upload_to_approval_days": float(pt.p95_upload_to_approval_days),
            "avg_upload_to_paid_days": float(pt.avg_upload_to_paid_days),
            "median_upload_to_paid_days": float(pt.median_upload_to_paid_days),
            "p95_upload_to_paid_days": float(pt.p95_upload_to_paid_days),
            "count_approval_leg": pt.count_approval_leg,
            "count_paid_leg": pt.count_paid_leg,
        },
        "approval_bottleneck": [
            {
                "approver_id": r.approver_id,
                "approver_name": r.approver_name,
                "pending_count": r.pending_count,
                "oldest_pending_days": float(r.oldest_pending_days),
                "avg_pending_days": float(r.avg_pending_days),
            }
            for r in bottleneck_rows[:10]
        ],
        "discount_capture": {
            "eligible_count": discount.eligible_count,
            "captured_count": discount.captured_count,
            "missed_count": discount.missed_count,
            "captured_amount": float(discount.captured_amount),
            "missed_amount": float(discount.missed_amount),
            "capture_rate_pct": float(discount.capture_rate_pct),
        },
    }
