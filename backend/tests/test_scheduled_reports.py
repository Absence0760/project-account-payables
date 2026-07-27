"""Scheduled report runner.

Tests the execution-of-one-schedule path: generate the CSV via the
exporters, email it via the configured adapter, bump next_run_at
on success / persist a failure marker on error. The cadence math
and the failure-cap behaviour live here too — they're the rules
that keep a broken provider from looping forever or a
mis-configured row from quietly never running.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scheduled_reports import (
    compute_next_run,
    execute_schedule,
)


def _schedule(
    *,
    cadence="daily",
    next_run_at=None,
    recipients=("ap@acme.com",),
    enabled=True,
    last_status=None,
    last_error=None,
    report_type="invoice_register",
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        name="Daily AP Register",
        report_type=report_type,
        cadence=cadence,
        recipients=list(recipients),
        period_days=30,
        enabled=enabled,
        next_run_at=next_run_at or datetime(2026, 5, 1, tzinfo=UTC),
        last_run_at=None,
        last_run_status=last_status,
        last_run_error=last_error,
    )


# ---------------------------------------------------------------------------
# compute_next_run
# ---------------------------------------------------------------------------


def test_compute_next_run_daily_adds_one_day():
    base = datetime(2026, 5, 10, tzinfo=UTC)
    assert compute_next_run("daily", base) == base + timedelta(days=1)


def test_compute_next_run_weekly_adds_seven_days():
    base = datetime(2026, 5, 10, tzinfo=UTC)
    assert compute_next_run("weekly", base) == base + timedelta(days=7)


def test_compute_next_run_monthly_adds_30_days():
    """Calendar-aware monthly would land on the 1st of next month;
    we approximate with 30 days. Operators pick "the 1st" via the
    `next_run_at` they set on creation, then this loop rolls it
    forward."""
    base = datetime(2026, 5, 10, tzinfo=UTC)
    assert compute_next_run("monthly", base) == base + timedelta(days=30)


def test_compute_next_run_unknown_cadence_falls_back_to_daily():
    """A garbage cadence shouldn't crash the runner — fall back to
    daily so the row keeps moving."""
    base = datetime(2026, 5, 10, tzinfo=UTC)
    # Capture the warning so the test doesn't pollute stderr.
    assert compute_next_run("yearly", base) == base + timedelta(days=1)


# ---------------------------------------------------------------------------
# execute_schedule — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_schedule_emails_recipients_and_bumps_next_run():
    """Happy path: generator returns a CSV string, email adapter
    sends to every recipient, DB row is updated with status=success
    and next_run_at advanced one cadence forward."""
    sched = _schedule(
        cadence="weekly",
        next_run_at=datetime(2026, 5, 1, tzinfo=UTC),
        recipients=["a@x.com", "b@x.com"],
    )
    db = AsyncMock()
    db.execute = AsyncMock()
    mock_adapter = MagicMock()
    mock_adapter.send = AsyncMock()
    now = datetime(2026, 5, 10, tzinfo=UTC)

    with (
        patch(
            "app.services.scheduled_reports._generate_report_payload",
            AsyncMock(return_value="csv,content,here\n"),
        ),
        patch(
            "app.services.scheduled_reports.get_email_adapter",
            return_value=mock_adapter,
        ),
    ):
        outcome = await execute_schedule(db, sched, now=now)

    assert outcome["status"] == "success"
    assert outcome["next_run_at"] == now + timedelta(days=7)
    # Both recipients got an email.
    assert mock_adapter.send.await_count == 2
    sent_to = {call.args[0].to for call in mock_adapter.send.call_args_list}
    assert sent_to == {"a@x.com", "b@x.com"}
    # The DB update was issued.
    db.execute.assert_awaited()


@pytest.mark.asyncio
async def test_execute_schedule_email_message_includes_payload_in_body():
    """The CSV payload rides on the email body — pin it so a
    regression doesn't ship a blank email when the generator works."""
    sched = _schedule(recipients=["dest@x.com"])
    db = AsyncMock()
    db.execute = AsyncMock()
    captured: list = []
    mock_adapter = MagicMock()

    async def _capture(msg):
        captured.append(msg)

    mock_adapter.send = AsyncMock(side_effect=_capture)

    with (
        patch(
            "app.services.scheduled_reports._generate_report_payload",
            AsyncMock(return_value="vendor,total\nAcme,1000\n"),
        ),
        patch(
            "app.services.scheduled_reports.get_email_adapter",
            return_value=mock_adapter,
        ),
    ):
        await execute_schedule(db, sched, now=datetime.now(UTC))

    assert len(captured) == 1
    body = captured[0].body_text
    assert "vendor,total" in body
    assert "Acme,1000" in body


# ---------------------------------------------------------------------------
# execute_schedule — failure paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_schedule_generator_error_persisted_not_raised():
    """The generator raises (bad report_type, DB error). The runner
    catches, persists the error, returns status=failure — does NOT
    propagate (a single broken schedule must not knock the loop
    over)."""
    sched = _schedule()
    db = AsyncMock()
    db.execute = AsyncMock()

    with patch(
        "app.services.scheduled_reports._generate_report_payload",
        AsyncMock(side_effect=ValueError("unknown report_type")),
    ):
        outcome = await execute_schedule(db, sched, now=datetime.now(UTC))

    assert outcome["status"] == "failure"
    assert "unknown report_type" in (outcome["error"] or "")


@pytest.mark.asyncio
async def test_execute_schedule_empty_recipients_fails_without_calling_email_adapter():
    """A schedule with no recipients shouldn't quietly succeed —
    surface the misconfig as a failure marker."""
    sched = _schedule(recipients=[])
    db = AsyncMock()
    db.execute = AsyncMock()
    mock_adapter = MagicMock()
    mock_adapter.send = AsyncMock()

    with (
        patch(
            "app.services.scheduled_reports._generate_report_payload",
            AsyncMock(return_value="csv"),
        ),
        patch(
            "app.services.scheduled_reports.get_email_adapter",
            return_value=mock_adapter,
        ),
    ):
        outcome = await execute_schedule(db, sched, now=datetime.now(UTC))

    assert outcome["status"] == "failure"
    assert "no recipients" in (outcome["error"] or "")
    mock_adapter.send.assert_not_called()


@pytest.mark.asyncio
async def test_execute_schedule_email_failure_sanitises_provider_message():
    """The email adapter's exception MUST NOT leak through into the
    persisted error string — SMTP transport errors sometimes echo
    recipient addresses or relay banners. We keep the class name,
    not the message."""
    sched = _schedule(recipients=["dest@x.com"])
    db = AsyncMock()
    db.execute = AsyncMock()
    mock_adapter = MagicMock()
    mock_adapter.send = AsyncMock(
        side_effect=RuntimeError("550 rejected from relay.foo.com to dest@x.com")
    )

    with (
        patch(
            "app.services.scheduled_reports._generate_report_payload",
            AsyncMock(return_value="csv"),
        ),
        patch(
            "app.services.scheduled_reports.get_email_adapter",
            return_value=mock_adapter,
        ),
    ):
        outcome = await execute_schedule(db, sched, now=datetime.now(UTC))

    assert outcome["status"] == "failure"
    # `email failed: RuntimeError` — class name, no leaked details.
    assert outcome["error"] == "email failed: RuntimeError"
    assert "relay.foo.com" not in (outcome["error"] or "")
    assert "dest@x.com" not in (outcome["error"] or "")


@pytest.mark.asyncio
async def test_execute_schedule_disables_after_repeated_failures():
    """Five consecutive failures → flip enabled=false so the queue
    stops banging on a broken provider. The DB write is the
    contract; we read the values from the issued UPDATE."""
    sched = _schedule(
        last_status="failure",
        last_error="[retry 4] prior error",
        recipients=["dest@x.com"],
    )
    db = AsyncMock()
    db.execute = AsyncMock()

    with patch(
        "app.services.scheduled_reports._generate_report_payload",
        AsyncMock(side_effect=ValueError("still broken")),
    ):
        outcome = await execute_schedule(db, sched, now=datetime.now(UTC))

    assert outcome["status"] == "failure"
    # The mark_failure UPDATE was issued; sniff the values.
    update_call = db.execute.await_args
    assert update_call is not None
    update_stmt = update_call.args[0]
    # The compiled statement parameters carry the bound values.
    params = update_stmt.compile().params
    assert params.get("enabled") is False
    assert "[retry 5]" in (params.get("last_run_error") or "")


@pytest.mark.asyncio
async def test_execute_schedule_first_failure_does_not_disable():
    """First failure → status=failure, but enabled stays True so
    the next sweep retries."""
    sched = _schedule(recipients=["dest@x.com"])
    db = AsyncMock()
    db.execute = AsyncMock()

    with patch(
        "app.services.scheduled_reports._generate_report_payload",
        AsyncMock(side_effect=ValueError("transient")),
    ):
        await execute_schedule(db, sched, now=datetime.now(UTC))

    update_call = db.execute.await_args
    update_stmt = update_call.args[0]
    params = update_stmt.compile().params
    # `enabled` should NOT be in the update values for a first
    # failure (only set when we hit the cap).
    assert "enabled" not in params
    assert "[retry 1]" in (params.get("last_run_error") or "")


# ---------------------------------------------------------------------------
# aging_snapshot bucketing — 61-90 (days_90) is its own bucket, not lumped 90+
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aging_snapshot_separates_61_90_from_90_plus():
    """Regression: the materializer built only 4 buckets, so 61-90-day invoices
    collapsed into 90+ and the CSV's days_90 column was always 0. It must
    produce the same 5 buckets the exporter (and the API export) expect."""
    from datetime import date
    from datetime import timedelta as td
    from decimal import Decimal

    from app.services.scheduled_reports import _materialise_rows

    today = date.today()
    rows = [
        (today + td(days=10), Decimal("10.00")),  # current (not yet due)
        (today - td(days=15), Decimal("20.00")),  # days_30
        (today - td(days=45), Decimal("30.00")),  # days_60
        (today - td(days=75), Decimal("40.00")),  # days_90 (61-90)
        (today - td(days=120), Decimal("50.00")),  # days_90_plus
    ]
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    captured: dict = {}

    def _exporter(buckets):
        captured.update(buckets)
        return "csv"

    sched = _schedule(report_type="aging_snapshot")
    out = await _materialise_rows(db, sched, _exporter)

    assert out == "csv"
    assert captured["days_60"] == Decimal("30.00")
    assert captured["days_90"] == Decimal("40.00")  # the previously-missing bucket
    assert captured["days_90_plus"] == Decimal("50.00")  # NOT 90.00 (40+50 lumped)


# ---------------------------------------------------------------------------
# Issue #120 — non-exhaustive dispatch silently fell through to aging_snapshot
# for any report_type without its own branch. `expense_register` schedules
# "succeeded" while emailing the aging bucket dict's string keys sliced
# character-by-character into the expense columns; `cashflow_forecast`
# schedules crashed every tick with an AttributeError and auto-disabled after
# 5 failures.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expense_register_dispatches_to_the_expense_query_not_aging():
    """A scheduled `expense_register` report must query Expense rows and hand
    them to the expense exporter — not fall through to the aging bucket
    dict."""
    from app.services.scheduled_reports import _materialise_rows

    fake_expense = SimpleNamespace(
        expense_date=None, merchant="Staples", category="office", amount=None, currency="USD"
    )
    rows = [(fake_expense, "EXP-1", "6100")]
    result = MagicMock()
    result.all = MagicMock(return_value=rows)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    captured: list = []

    def _exporter(materialised_rows):
        captured.extend(materialised_rows)
        return "csv"

    sched = _schedule(report_type="expense_register")
    out = await _materialise_rows(db, sched, _exporter)

    assert out == "csv"
    # The exporter received the real (Expense, report_number, gl_code) tuple —
    # not the aging bucket dict's items.
    assert captured == rows


@pytest.mark.asyncio
async def test_cashflow_forecast_dispatches_to_the_commitment_query_not_aging():
    """A scheduled `cashflow_forecast` report must run the commitment-rows +
    bucket_outflows pipeline — not the aging-snapshot query (which used to
    feed a bucket dict into the cashflow exporter and crash)."""
    from app.services.scheduled_reports import _materialise_rows

    # Shape consumed by `_commitment_rows`:
    # (amount, status, invoice due_date, sched_due, discount_date, discount_percent)
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    captured: list = []

    def _exporter(periods):
        captured.append(periods)
        return "csv"

    sched = _schedule(report_type="cashflow_forecast")
    out = await _materialise_rows(db, sched, _exporter)

    assert out == "csv"
    # Got a (possibly empty) list of period dicts from bucket_outflows, not a
    # bucket dict with "current"/"days_30"/... keys.
    assert isinstance(captured[0], list)


@pytest.mark.asyncio
async def test_unrecognised_report_type_raises_instead_of_silently_defaulting():
    """A report_type with no dispatch branch must raise loudly, never fall
    through to the aging_snapshot path — the exact bug this replaces."""
    from app.services.scheduled_reports import _materialise_rows

    db = AsyncMock()
    sched = _schedule(report_type="totally_unknown_type")

    with pytest.raises(ValueError, match="totally_unknown_type"):
        await _materialise_rows(db, sched, lambda rows: "csv")


# ---------------------------------------------------------------------------
# run_scheduled_reports_once — tenant fan-out + failure isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_aggregates_and_isolates_tenant_failures():
    """The sweep iterates every tenant, sums the per-tenant run/fail counts, and
    one tenant blowing up must not halt the others."""
    from app.services import scheduled_reports as sr

    ctrl = AsyncMock()
    ctrl_result = MagicMock()
    ctrl_result.all = MagicMock(return_value=[("feoh_a",), ("feoh_b",)])
    ctrl.execute = AsyncMock(return_value=ctrl_result)
    ctrl_cm = MagicMock()
    ctrl_cm.__aenter__ = AsyncMock(return_value=ctrl)
    ctrl_cm.__aexit__ = AsyncMock(return_value=False)

    async def _fake_sweep(db_name, *, now):
        if db_name == "feoh_a":
            return (2, 0)  # 2 schedules ran, none failed
        raise RuntimeError("tenant b is broken")

    with (
        patch("app.database.control_session_factory", MagicMock(return_value=ctrl_cm)),
        patch("app.services.scheduled_reports._sweep_tenant", _fake_sweep),
    ):
        result = await sr.run_scheduled_reports_once()

    assert result.tenants_scanned == 2
    assert result.schedules_run == 2
    assert result.failures == 1  # feoh_b's exception counted, feoh_a still ran
