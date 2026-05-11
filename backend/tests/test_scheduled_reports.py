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
