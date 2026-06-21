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
from dataclasses import dataclass
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
    # Five buckets matching the exporter + the dashboard/analytics scheme
    # (current / 1-30 / 31-60 / 61-90 / 90+). The 61-90 (`days_90`) bucket was
    # missing here, so 61-90-day invoices collapsed into 90+ and the CSV's
    # days_90 column was always 0 — disagreeing with the API export.
    buckets = {
        "current": _D("0"),
        "days_30": _D("0"),
        "days_60": _D("0"),
        "days_90": _D("0"),
        "days_90_plus": _D("0"),
    }
    for due, amt in aging_rows.all():
        days_past = (today - due).days
        amount = _D(str(amt))
        if days_past <= 0:
            buckets["current"] += amount
        elif days_past <= 30:
            buckets["days_30"] += amount
        elif days_past <= 60:
            buckets["days_60"] += amount
        elif days_past <= 90:
            buckets["days_90"] += amount
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


# ---------------------------------------------------------------------------
# Tenant-fan-out sweep + long-lived loop (mirrors contract_renewal /
# recurring_invoices). Without this the per-schedule machinery above was never
# invoked — scheduled reports sat due forever and no email ever went out.
# ---------------------------------------------------------------------------


@dataclass
class SweepResult:
    """Per-sweep outcome for logging + tests."""

    tenants_scanned: int = 0
    schedules_run: int = 0
    failures: int = 0


async def _sweep_tenant(db_name: str, *, now: datetime) -> tuple[int, int]:
    """Run every due schedule for one tenant. Returns (run, failed)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.database import _make_tenant_url

    engine = create_async_engine(_make_tenant_url(db_name))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    run = 0
    failed = 0
    try:
        async with factory() as db:
            due = await list_due_schedules(db, now=now)
            for schedule in due:
                outcome = await execute_schedule(db, schedule, now=now)
                run += 1
                if outcome["status"] == "failure":
                    failed += 1
            if due:
                await db.commit()
    finally:
        await engine.dispose()
    return run, failed


async def run_scheduled_reports_once(*, now: datetime | None = None) -> SweepResult:
    """One sweep across every tenant. Safe to call directly (CLI / tests)."""
    from app.database import control_session_factory
    from app.models.organization import Organization

    ref_now = now or datetime.now(UTC)
    result = SweepResult()

    async with control_session_factory() as ctrl:
        rows = await ctrl.execute(select(Organization.db_name))
        db_names = [r[0] for r in rows.all()]

    for db_name in db_names:
        result.tenants_scanned += 1
        try:
            run, failed = await _sweep_tenant(db_name, now=ref_now)
            result.schedules_run += run
            result.failures += failed
        except Exception as exc:  # noqa: BLE001 — one tenant must not halt the sweep
            logger.warning("[scheduled_reports] failed sweeping %s: %s", db_name, exc)
            result.failures += 1

    if result.schedules_run or result.failures:
        logger.info(
            "[scheduled_reports] swept %d tenant(s); ran=%d failed=%d",
            result.tenants_scanned,
            result.schedules_run,
            result.failures,
        )
    return result


async def run_scheduled_reports_loop() -> None:
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown."""
    import asyncio

    from app.config import settings

    interval = settings.scheduled_reports_tick_seconds
    logger.info("[scheduled_reports] started; interval=%ds", interval)
    try:
        while True:
            try:
                await run_scheduled_reports_once()
            except Exception as exc:  # noqa: BLE001
                logger.error("[scheduled_reports] sweep raised: %s", exc, exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("[scheduled_reports] shutting down")
        raise
