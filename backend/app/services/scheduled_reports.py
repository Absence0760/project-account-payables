"""Scheduled-report runner.

Runs on the same cadence-via-asyncio pattern as
`extraction_reaper` / `payment_reconciler` / `audit_log_shipper`:
one long-lived task per app process, started in `main.lifespan`,
ticks every `AP_SCHEDULED_REPORTS_TICK_SECONDS`, sweeps every
tenant DB for `scheduled_reports` rows whose `next_run_at <= now`
and `enabled = true`, generates the CSV via `report_export`,
emails it to the listed recipients, then bumps `next_run_at` by
the cadence.

Cadences:
  - daily: next_run += 1 day
  - weekly: next_run += 7 days
  - monthly: next_run += ~30 days (calendar-aware would be nicer
    but a separate piece — operators typically pick "the 1st of
    the month" + daily cadence)

Failures don't block: `last_run_status='failure'` + truncated
error message is saved, next_run_at stays at the original time so
the next tick retries. Repeated failures cap at 5 retries by
flipping `enabled=false` (so the queue doesn't loop forever on a
broken provider). Operators re-enable from the admin UI after
fixing.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scheduled_report import ScheduledReport
from app.services.email_adapters import EmailMessage, get_email_adapter

logger = logging.getLogger(__name__)


_CADENCE_DELTA = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
}


def compute_next_run(cadence: str, from_dt: datetime) -> datetime:
    """Add the cadence's delta to `from_dt`. Unknown cadences fall
    back to daily so a stale legacy row doesn't poison the
    scheduler — a WARN log surfaces the misconfig."""
    delta = _CADENCE_DELTA.get(cadence)
    if delta is None:
        logger.warning("[scheduled_reports] unknown cadence %r; falling back to daily", cadence)
        delta = _CADENCE_DELTA["daily"]
    return from_dt + delta


async def list_due_schedules(
    db: AsyncSession, *, now: datetime | None = None
) -> list[ScheduledReport]:
    """Pull every enabled schedule that's due. Used by the runner
    loop; exposed as a separate function so a test can drive it
    without poking at internal state."""
    now = now or datetime.now(UTC)
    result = await db.execute(
        select(ScheduledReport).where(
            ScheduledReport.enabled.is_(True),
            ScheduledReport.next_run_at <= now,
        )
    )
    return list(result.scalars().all())


async def _generate_report_payload(
    db: AsyncSession,
    schedule: ScheduledReport,
) -> str:
    """Pull the rows + serialise via `report_export`. We import the
    exporters lazily because the test surface doesn't always need
    SQLAlchemy models in scope — a unit test can short-circuit by
    monkey-patching this function on the module."""
    from app.services.report_export import EXPORTERS

    exporter = EXPORTERS.get(schedule.report_type)
    if exporter is None:
        raise ValueError(f"unknown report_type {schedule.report_type!r}")

    # Lazy SQL — same shapes as the API export endpoint. We
    # delegate to a small helper per report so this function stays
    # short.
    return await _materialise_rows(db, schedule, exporter)


async def _materialise_rows(
    db: AsyncSession,
    schedule: ScheduledReport,
    exporter: Callable,
) -> str:
    from datetime import date
    from datetime import datetime as _dt

    from sqlalchemy import func

    from app.models.invoice import Invoice
    from app.models.payment import Payment

    period_start = date.today() - timedelta(days=schedule.period_days)

    if schedule.report_type == "invoice_register":
        rows = await db.execute(select(Invoice).where(Invoice.invoice_date >= period_start))
        return exporter(rows.scalars().all())

    if schedule.report_type == "vendor_spend":
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
        return exporter(rows.all())

    if schedule.report_type == "payment_register":
        rows = await db.execute(
            select(Payment, Invoice)
            .outerjoin(Invoice, Invoice.id == Payment.invoice_id)
            .where(
                Payment.created_at >= _dt.combine(period_start, _dt.min.time()).replace(tzinfo=UTC)
            )
        )
        return exporter(rows.all())

    # aging_snapshot doesn't paginate by period — always as-of-now.
    from decimal import Decimal as _D

    today = date.today()
    open_statuses = ("new", "pending", "ready_for_review", "approved")
    aging_rows = await db.execute(
        select(Invoice.due_date, Invoice.amount).where(
            Invoice.status.in_(open_statuses),
            Invoice.due_date.isnot(None),
        )
    )
    buckets = {"current": _D("0"), "days_30": _D("0"), "days_60": _D("0"), "days_90_plus": _D("0")}
    for due, amt in aging_rows.all():
        days_past = (today - due).days
        amount = _D(str(amt))
        if days_past <= 0:
            buckets["current"] += amount
        elif days_past <= 30:
            buckets["days_30"] += amount
        elif days_past <= 60:
            buckets["days_60"] += amount
        else:
            buckets["days_90_plus"] += amount
    return exporter(buckets)


async def execute_schedule(
    db: AsyncSession,
    schedule: ScheduledReport,
    *,
    now: datetime | None = None,
) -> dict:
    """Run one schedule end-to-end: generate, email, update the
    bookkeeping. Returns a small dict the caller / tests can
    assert on:
      `{"status": "success" | "failure", "error": str | None,
        "next_run_at": datetime}`.
    The function NEVER raises — failures are surfaced as the
    "failure" status with an error message, AND persisted on the
    row, so the runner loop can keep ticking.
    """
    now = now or datetime.now(UTC)
    try:
        payload = await _generate_report_payload(db, schedule)
    except Exception as exc:  # noqa: BLE001
        err = str(exc)[:500]
        await _mark_failure(db, schedule, err, now)
        return {"status": "failure", "error": err, "next_run_at": schedule.next_run_at}

    if not schedule.recipients:
        err = "no recipients configured"
        await _mark_failure(db, schedule, err, now)
        return {"status": "failure", "error": err, "next_run_at": schedule.next_run_at}

    adapter = get_email_adapter()
    try:
        for recipient in schedule.recipients:
            await adapter.send(
                EmailMessage(
                    to=str(recipient),
                    subject=f"AP Report: {schedule.name}",
                    body_text=(
                        f"Attached: {schedule.report_type} for the trailing "
                        f"{schedule.period_days} days.\n\n"
                        f"{payload}\n"
                    ),
                    body_html=None,
                )
            )
    except Exception as exc:  # noqa: BLE001
        # Email-adapter exceptions can carry SMTP transport details
        # which sometimes echo recipient addresses back; we truncate
        # but otherwise preserve so the AP team can debug.
        err = f"email failed: {exc.__class__.__name__}"
        await _mark_failure(db, schedule, err, now)
        return {"status": "failure", "error": err, "next_run_at": schedule.next_run_at}

    # Success — bump next_run_at by the cadence.
    next_run = compute_next_run(schedule.cadence, now)
    await db.execute(
        update(ScheduledReport)
        .where(ScheduledReport.id == schedule.id)
        .values(
            last_run_at=now,
            last_run_status="success",
            last_run_error=None,
            next_run_at=next_run,
            updated_at=now,
        )
    )
    return {"status": "success", "error": None, "next_run_at": next_run}


async def _mark_failure(
    db: AsyncSession,
    schedule: ScheduledReport,
    err: str,
    now: datetime,
) -> None:
    """Persist a failure marker. next_run_at stays untouched so the
    next sweep retries (until 5 consecutive failures, then we
    disable — the operator re-enables after fixing)."""
    cap = 5
    consecutive = 1
    if schedule.last_run_status == "failure":
        # `last_run_error` may carry a count prefix from a prior tick.
        prior = schedule.last_run_error or ""
        if prior.startswith("[retry "):
            try:
                consecutive = int(prior.split("[retry ")[1].split("]")[0]) + 1
            except (ValueError, IndexError):
                consecutive = 2

    decorated = f"[retry {consecutive}] {err}"[:500]
    values = {
        "last_run_at": now,
        "last_run_status": "failure",
        "last_run_error": decorated,
        "updated_at": now,
    }
    if consecutive >= cap:
        values["enabled"] = False
    await db.execute(
        update(ScheduledReport).where(ScheduledReport.id == schedule.id).values(**values),
    )
