"""Scheduled-report runner.

Runs on the same cadence-via-asyncio pattern as
`extraction_reaper` / `payment_reconciler` / `audit_log_shipper`:
one long-lived task per app process, started in `main.lifespan`,
ticks every `FEOH_SCHEDULED_REPORTS_TICK_SECONDS`, sweeps every
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
broken provider). An operator re-enables it after fixing — today by
flipping the column directly: nothing under `app/api/` references
`ScheduledReport`, so this table has no CRUD surface yet (see
`docs/analytics.md` § Scheduled report delivery).

Delivery is per-recipient, so `last_run_status` has three values:

  - `success` — every recipient took the report; `next_run_at` bumped.
  - `partial` — some did, some didn't. `next_run_at` is bumped ANYWAY,
    because a retry would redeliver to the recipients who already have
    it; the count of failures rides on `last_run_error`. A partial does
    NOT accumulate toward the 5-strike auto-disable — one bad address
    must not disable a schedule that is still reaching everyone else.
  - `failure` — generation failed, no recipients are configured, or
    NOBODY took it. `next_run_at` untouched; the next tick retries.
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


# Report types whose rows are money summed across possibly-different invoice
# currencies, so they need the org's reporting currency resolved up front.
_REPORTING_CURRENCY_REPORTS = frozenset({"vendor_spend", "cashflow_forecast"})


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

    # Resolve the org's reporting currency ONCE, here, where we legitimately
    # hold a control-plane session — the money-rollup branches take a tenant
    # session and must not reach past it (see `_materialise_rows`).
    reporting_currency: str | None = None
    if schedule.report_type in _REPORTING_CURRENCY_REPORTS:
        from app.database import control_session_factory
        from app.models.organization import Organization
        from app.services.currency_conversion import resolve_reporting_currency

        async with control_session_factory() as ctrl_db:
            org = (
                await ctrl_db.execute(
                    select(Organization).where(Organization.id == schedule.organization_id)
                )
            ).scalar_one_or_none()
        reporting_currency = resolve_reporting_currency(org.settings if org else None)

    # Lazy SQL — same shapes as the API export endpoint. We
    # delegate to a small helper per report so this function stays
    # short.
    return await _materialise_rows(db, schedule, exporter, reporting_currency)


async def _materialise_rows(
    db: AsyncSession,
    schedule: ScheduledReport,
    exporter: Callable,
    reporting_currency: str | None = None,
) -> str:
    """Pull the rows for one report and hand them to its exporter.

    ``reporting_currency`` is supplied by the caller that already knows the org
    (``_generate_report_payload``). It is a parameter rather than a lookup
    inside the money-rollup branches because those branches take a TENANT
    session: opening a control-plane connection here reaches past the session
    the caller handed us, which a caller holding a mocked or tenant-only
    session cannot intercept. Absent, the branches fall back to the documented
    platform default — a pure resolution with no database hit.
    """
    from datetime import date
    from datetime import datetime as _dt

    from app.models.invoice import Invoice, InvoiceStatus
    from app.models.payment import Payment

    period_start = date.today() - timedelta(days=schedule.period_days)

    if schedule.report_type == "invoice_register":
        rows = await db.execute(select(Invoice).where(Invoice.invoice_date >= period_start))
        return exporter(rows.scalars().all())

    if schedule.report_type == "vendor_spend":
        from app.services.currency_conversion import (
            resolve_reporting_currency,
            vendor_rollup_to_reporting_currency,
        )

        # Same as the cashflow branch below: the currency comes from the
        # caller, so this branch never opens a control-plane connection behind
        # the tenant session it was handed.
        rollup_currency = reporting_currency or resolve_reporting_currency(None)

        rows = await db.execute(
            select(
                Invoice.vendor_name,
                Invoice.amount,
                Invoice.currency,
                Invoice.reporting_amount,
                Invoice.reporting_currency,
            ).where(
                Invoice.invoice_date >= period_start,
                Invoice.vendor_name.isnot(None),
                Invoice.vendor_name != "",
                # Same population as the CFO concentration tile
                # (get_cfo_analytics) and its API export — rejected invoices
                # were never real spend.
                Invoice.status != InvoiceStatus.rejected.value,
            )
        )
        # Rolled into the org's reporting currency (not a naive SUM across
        # currencies) — a vendor billing in more than one currency used to
        # add e.g. USD + EUR as if they were one currency.
        vendor_entries = vendor_rollup_to_reporting_currency(
            [
                {
                    "vendor": vendor,
                    "amount": amount,
                    "currency": currency,
                    "reporting_amount": rep_amt,
                    "reporting_currency": rep_cur,
                }
                for vendor, amount, currency, rep_amt, rep_cur in rows.all()
            ],
            reporting_currency=rollup_currency,
        )
        return exporter(vendor_entries)

    if schedule.report_type == "payment_register":
        rows = await db.execute(
            select(Payment, Invoice)
            .outerjoin(Invoice, Invoice.id == Payment.invoice_id)
            .where(
                Payment.created_at >= _dt.combine(period_start, _dt.min.time()).replace(tzinfo=UTC)
            )
        )
        return exporter(rows.all())

    if schedule.report_type == "expense_register":
        from app.models.expense import Expense, ExpenseReport
        from app.models.gl_account import GLAccount

        # Same shape as `api/expenses.py::export_expenses` / the analytics
        # export endpoint: outer-joined so an uncoded / unattached expense
        # still emits a row.
        rows = await db.execute(
            select(Expense, ExpenseReport.report_number, GLAccount.code)
            .outerjoin(GLAccount, GLAccount.id == Expense.gl_account_id)
            .outerjoin(ExpenseReport, ExpenseReport.id == Expense.report_id)
            .where(Expense.expense_date >= period_start)
        )
        return exporter(rows.all())

    if schedule.report_type == "cashflow_forecast":
        from app.api.analytics import _commitment_rows
        from app.config import settings
        from app.services.analytics import bucket_outflows
        from app.services.currency_conversion import resolve_reporting_currency

        # ScheduledReport has no per-schedule granularity/horizon — mirror the
        # API export endpoint's own defaults (`granularity="week"`,
        # `horizon_days` from the same platform default the copilot uses).
        today = date.today()
        # Outflows are expressed in the org's reporting currency, never summed
        # raw across whatever currencies its suppliers happen to bill in. The
        # currency comes from the caller (see this function's docstring) so
        # this branch never reaches past the session it was handed.
        commitment_rows = await _commitment_rows(
            db,
            today=today,
            horizon_days=settings.cashflow_copilot_default_horizon_days,
            include_pending=True,
            reporting_currency=reporting_currency or resolve_reporting_currency(None),
        )
        periods = bucket_outflows(commitment_rows, granularity="week", today=today)
        return exporter(periods)

    if schedule.report_type == "aging_snapshot":
        from decimal import Decimal as _D

        from app.services.analytics import OPEN_AP_STATUSES

        today = date.today()
        # Same open-payable population as the AP balance + the API aging export
        # so the emailed snapshot reconciles with them (F-4): approved →
        # payment_scheduled, not the pre-approval statuses. The AP balance has
        # no due_date filter, so this must not either — an open invoice missing
        # a due date used to inflate the balance while vanishing from every
        # bucket.
        aging_rows = await db.execute(
            select(Invoice.due_date, Invoice.amount).where(
                Invoice.status.in_(OPEN_AP_STATUSES),
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
            amount = _D(str(amt))
            # A null due_date can't be judged overdue — bucket as "current" (the
            # conservative read) rather than dropping it entirely.
            if due is None:
                buckets["current"] += amount
                continue
            days_past = (today - due).days
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

    # Unreachable in practice — every key registered in EXPORTERS (checked by
    # `_generate_report_payload` above) has a branch here. A hard guard
    # against exactly the bug this replaces: a report_type with no matching
    # branch used to silently fall through to the aging_snapshot path instead
    # of failing loudly (feeding aging's bucket dict to an exporter expecting
    # a row list — e.g. iterating the dict's string keys character-by-character
    # into `expense_register`'s columns, or an AttributeError crash loop for
    # `cashflow_forecast`).
    raise ValueError(
        f"report_type {schedule.report_type!r} is registered in EXPORTERS but has no "
        "dispatch branch in _materialise_rows"
    )


async def execute_schedule(
    db: AsyncSession,
    schedule: ScheduledReport,
    *,
    now: datetime | None = None,
) -> dict:
    """Run one schedule end-to-end: generate, email, update the
    bookkeeping. Returns a small dict the caller / tests can
    assert on:
      `{"status": "success" | "partial" | "failure", "error": str | None,
        "next_run_at": datetime}`.
    The function NEVER raises — failures are surfaced as the
    "failure" status with an error message, AND persisted on the
    row, so the runner loop can keep ticking. See the module docstring
    for what separates `partial` from `failure`.
    """
    now = now or datetime.now(UTC)
    try:
        payload = await _generate_report_payload(db, schedule)
    except Exception as exc:  # noqa: BLE001
        # Class name only, never `str(exc)`. A DB-level error message echoes
        # the offending row's values, and `last_run_error` is surfaced to
        # every AP user — the same reason the email branch below already
        # stores only the class (PII-out-of-error-responses invariant).
        err = f"report generation failed: {exc.__class__.__name__}"
        await _mark_failure(db, schedule, err, now)
        return {"status": "failure", "error": err, "next_run_at": schedule.next_run_at}

    if not schedule.recipients:
        err = "no recipients configured"
        await _mark_failure(db, schedule, err, now)
        return {"status": "failure", "error": err, "next_run_at": schedule.next_run_at}

    # Each recipient is an INDEPENDENT delivery — one address failing must not
    # abort the ones after it, and must not replay the ones before it.
    #
    # The loop used to be wrapped in a single try/except: a failure at
    # recipient 2 of 5 skipped 3-5 entirely AND left `next_run_at` untouched,
    # so the next tick regenerated the same report and re-sent it to recipient
    # 1 — up to five times before the auto-disable, while 3-5 never received it
    # once and then lost the schedule altogether.
    adapter = get_email_adapter()
    delivered = 0
    failed = 0
    # Only the exception CLASS is kept. An SMTP transport error echoes relay
    # banners and recipient addresses, and `last_run_error` is surfaced to
    # every AP user (PII-out-of-error-responses invariant).
    last_error_class: str | None = None
    for recipient in schedule.recipients:
        try:
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
        except Exception as exc:  # noqa: BLE001 — one address must not stop the rest
            failed += 1
            last_error_class = exc.__class__.__name__
            continue
        delivered += 1

    if delivered == 0:
        # Nobody received this period's report, so a retry is free of
        # duplicates — leave `next_run_at` alone and let the next tick try
        # again (auto-disabling after the 5th consecutive failure).
        err = f"email failed: {last_error_class}"
        await _mark_failure(db, schedule, err, now)
        return {"status": "failure", "error": err, "next_run_at": schedule.next_run_at}

    # At least one recipient HAS the report for this period. Bump `next_run_at`
    # either way: replaying the run to redeliver to the failures would send a
    # duplicate to everyone who already got it, and the report is a periodic
    # snapshot, not a transaction that must reach every party. A persistently
    # bad address is an operator fix (remove/correct it), which is why a
    # partial does NOT accumulate toward the 5-strike auto-disable — disabling
    # the schedule would punish the recipients it is still reaching.
    next_run = compute_next_run(schedule.cadence, now)
    if failed:
        status = "partial"
        total = delivered + failed
        err = f"email failed for {failed} of {total} recipients: {last_error_class}"[:500]
    else:
        status = "success"
        err = None
    await db.execute(
        update(ScheduledReport)
        .where(ScheduledReport.id == schedule.id)
        .values(
            last_run_at=now,
            last_run_status=status,
            last_run_error=err,
            next_run_at=next_run,
            updated_at=now,
        )
    )
    return {"status": status, "error": err, "next_run_at": next_run}


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
                # One transaction PER SCHEDULE. Sharing one across the tenant's
                # whole batch meant a DB-level error in schedule N (a statement
                # timeout, a tenant behind on a migration) aborted the
                # transaction, so `_mark_failure`'s own write raised
                # `PendingRollbackError` and unwound the loop — rolling back
                # the `next_run_at` bumps of every schedule that had ALREADY
                # generated and EMAILED successfully. Those re-sent their
                # report on every tick, forever, while the failing one's
                # retry counter never persisted so the documented 5-strike
                # auto-disable could never fire. Same shape
                # `recurring_invoices` and `vendor_rescreen` already adopted.
                try:
                    outcome = await execute_schedule(db, schedule, now=now)
                    await db.commit()
                except Exception:  # noqa: BLE001
                    # `execute_schedule` promises not to raise, but its own
                    # bookkeeping write can when the session is already
                    # poisoned. Roll back so the NEXT schedule starts clean.
                    await db.rollback()
                    logger.warning(
                        "[scheduled-reports] schedule %s failed in tenant %s",
                        schedule.id,
                        db_name,
                    )
                    run += 1
                    failed += 1
                    continue
                run += 1
                # A `partial` counts too: some recipient did not get the
                # report, which `sweep_health` should surface rather than
                # round down to "healthy" (the exact blind spot the sweep
                # registry exists to close).
                if outcome["status"] != "success":
                    failed += 1
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
            # Log the exception CLASS only — a DB/SMTP error message can echo a
            # vendor name / recipient address / partial SQL value (PII-out-of-logs).
            logger.warning(
                "[scheduled_reports] failed sweeping %s: %s", db_name, exc.__class__.__name__
            )
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
    """Long-lived loop started in ``main.lifespan``; cancelled on shutdown.
    Body is the shared ``sweep_health.run_sweep_loop``."""
    from app.config import settings
    from app.services.sweep_health import SWEEP_SCHEDULED_REPORTS, run_sweep_loop

    await run_sweep_loop(
        SWEEP_SCHEDULED_REPORTS,
        lambda: run_scheduled_reports_once(),
        interval_seconds=settings.scheduled_reports_tick_seconds,
        log=logger,
        log_prefix="[scheduled_reports]",
    )
