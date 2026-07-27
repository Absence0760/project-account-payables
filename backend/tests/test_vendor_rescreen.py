"""Tests for the periodic vendor re-screening sweep ("ongoing monitoring").

Two layers, mirroring the other background-sweep suites:

* DB-free unit tests patch the control session + ``_sweep_tenant`` to assert on
  tenant iteration, partial-failure tolerance, and the long-lived loop's
  cancel / survive-a-bad-sweep behaviour.

* ``realdb`` tests prove the core re-screen behaviour against live Postgres +
  the real ``vendors`` / ``sanctions_checks`` tables and the default ``mock``
  sanctions adapter: a never-screened active vendor gets a ``periodic`` trail
  row + a fresh ``last_screened_at``; a recently-screened vendor is skipped; a
  stale one is re-screened; and a name on the mock SDN blocklist flips to
  ``match`` + ``payments_blocked`` and counts as a new flag.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.sanctions_check import SanctionsCheck
from app.models.vendor import Vendor
from app.services import vendor_rescreen
from app.services.vendor_rescreen import rescreen_vendors_once

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_control_session(tenant_db_names: list[str]):
    """Async context manager whose ``execute().all()`` yields
    (org_id, db_name, settings) rows."""
    fake_rows = [(uuid.uuid4(), n, {}) for n in tenant_db_names]
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(all=lambda: fake_rows))

    cm = AsyncMock()
    cm.__aenter__.return_value = fake_session
    cm.__aexit__.return_value = None
    return MagicMock(return_value=cm)


async def _seed_vendor(realdb, key: str, **overrides) -> uuid.UUID:
    """Insert one vendor into ``key``'s tenant DB. Returns its id."""
    org_id = realdb.info(key).org_id
    vid = uuid.uuid4()
    fields = {
        "id": vid,
        "name": f"Vendor {vid.hex[:8]}",
        "status": "active",
        "organization_id": org_id,
    }
    fields.update(overrides)
    mk = realdb.sessionmaker(key)
    async with mk() as s:
        s.add(Vendor(**fields))
        await s.commit()
    return vid


async def _get_vendor(realdb, key: str, vid: uuid.UUID) -> Vendor:
    mk = realdb.sessionmaker(key)
    async with mk() as s:
        return (await s.execute(select(Vendor).where(Vendor.id == vid))).scalar_one()


async def _checks_for(realdb, key: str, vid: uuid.UUID) -> list[SanctionsCheck]:
    mk = realdb.sessionmaker(key)
    async with mk() as s:
        return (
            (await s.execute(select(SanctionsCheck).where(SanctionsCheck.vendor_id == vid)))
            .scalars()
            .all()
        )


# ---------------------------------------------------------------------------
# DB-free unit tests — sweep orchestration
# ---------------------------------------------------------------------------


async def test_rescreen_once_iterates_every_tenant():
    with (
        patch.object(
            vendor_rescreen,
            "control_session_factory",
            _fake_control_session(["feoh_a", "feoh_b", "feoh_c"]),
        ),
        patch.object(
            vendor_rescreen, "_sweep_tenant", AsyncMock(return_value=(2, 1))
        ) as sweep_tenant,
    ):
        result = await rescreen_vendors_once()

    assert result.tenants_scanned == 3
    assert result.vendors_screened == 6  # 3 tenants × 2
    assert result.new_flags == 3  # 3 tenants × 1
    assert result.failures == 0
    assert sweep_tenant.await_count == 3


async def test_rescreen_once_continues_after_one_tenant_fails():
    """One bad tenant DB must not halt the sweep — log + move on."""
    side_effects = [(2, 0), RuntimeError("connection refused"), (1, 1)]
    with (
        patch.object(
            vendor_rescreen,
            "control_session_factory",
            _fake_control_session(["feoh_a", "feoh_b", "feoh_c"]),
        ),
        patch.object(vendor_rescreen, "_sweep_tenant", AsyncMock(side_effect=side_effects)),
    ):
        result = await rescreen_vendors_once()

    assert result.tenants_scanned == 3
    assert result.vendors_screened == 3  # 2 + (skipped) + 1
    assert result.new_flags == 1
    assert result.failures == 1


# A sentinel that stands in for the vendor name / partial banking value an
# asyncpg or sanctions-adapter error can carry in ``str(exc)``. It must never
# reach a log record (PII-out-of-logs invariant) — only the exception CLASS may.
_PII_SENTINEL = "SECRET_VENDOR_BANK_123"


async def test_sweep_failure_logs_exception_class_not_message(caplog):
    """A per-tenant sweep failure logs the exception CLASS only — the raw
    message (which can carry a vendor name / partial bank value) never lands in
    the log (CloudWatch), honouring the PII-out-of-logs invariant."""
    with (
        patch.object(
            vendor_rescreen,
            "control_session_factory",
            _fake_control_session(["feoh_a"]),
        ),
        patch.object(
            vendor_rescreen,
            "_sweep_tenant",
            AsyncMock(side_effect=RuntimeError(_PII_SENTINEL)),
        ),
        caplog.at_level(logging.WARNING, logger=vendor_rescreen.logger.name),
    ):
        result = await rescreen_vendors_once()

    assert result.failures == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING log for the failed sweep"
    for record in caplog.records:
        assert _PII_SENTINEL not in record.getMessage()
    assert any("RuntimeError" in r.getMessage() for r in warnings)


async def test_loop_failure_logs_exception_class_not_message(caplog):
    """The long-lived loop's top-level catch also logs the exception CLASS only
    (with exc_info for the traceback), never the raw message."""

    async def flaky():
        raise RuntimeError(_PII_SENTINEL)

    with (
        patch.object(vendor_rescreen, "rescreen_vendors_once", flaky),
        patch.object(vendor_rescreen.settings, "vendor_rescreen_interval_seconds", 0.01),
        caplog.at_level(logging.ERROR, logger=vendor_rescreen.logger.name),
    ):
        task = asyncio.create_task(vendor_rescreen.run_vendor_rescreen_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert errors, "expected an ERROR log for the failed sweep"
    # The format string must not carry the raw message. exc_info attaches the
    # traceback out-of-band; getMessage() returns only the rendered format
    # string, which is what ships as the CloudWatch message field.
    for record in errors:
        assert _PII_SENTINEL not in record.getMessage()
    assert any("RuntimeError" in r.getMessage() for r in errors)


async def test_run_loop_cancels_cleanly():
    with patch.object(
        vendor_rescreen, "rescreen_vendors_once", AsyncMock(return_value=SimpleNamespace())
    ):
        task = asyncio.create_task(vendor_rescreen.run_vendor_rescreen_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_run_loop_survives_a_failed_sweep():
    """A raise inside rescreen_vendors_once must not kill the long-lived loop."""
    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return SimpleNamespace()

    with (
        patch.object(vendor_rescreen, "rescreen_vendors_once", flaky),
        patch.object(vendor_rescreen.settings, "vendor_rescreen_interval_seconds", 0.01),
    ):
        task = asyncio.create_task(vendor_rescreen.run_vendor_rescreen_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert call_count >= 2  # didn't die on the first raise


# ---------------------------------------------------------------------------
# realdb tests — re-screen behaviour against live Postgres
# ---------------------------------------------------------------------------
#
# ``rescreen_vendors_once`` enumerates tenants via the module-global
# ``control_session_factory`` (a single process-wide engine). Under
# ``asyncio_mode=auto`` each test runs on a fresh event loop, so that cached
# engine's pooled connection belongs to a prior loop — reusing it raises
# "another operation is in progress". We point the sweep at a fresh control
# engine bound to THIS test's loop (the same trick the realdb harness uses for
# its own per-call session makers).


def _patch_control(realdb):
    """Context manager: route the sweep's control query through a fresh
    per-test control engine (loop-local)."""
    return patch.object(vendor_rescreen, "control_session_factory", realdb.control_sessionmaker())


async def test_never_screened_active_vendor_is_screened(realdb):
    """A vendor with last_screened_at IS NULL is due → screened, trail row
    written with check_type='periodic', last_screened_at stamped."""
    vid = await _seed_vendor(realdb, "a", name="Acme Clean Co")

    with _patch_control(realdb):
        result = await rescreen_vendors_once()

    assert result.vendors_screened >= 1
    vendor = await _get_vendor(realdb, "a", vid)
    assert vendor.last_screened_at is not None
    assert vendor.screening_status == "clear"
    checks = await _checks_for(realdb, "a", vid)
    assert len(checks) == 1
    assert checks[0].check_type == "periodic"
    assert checks[0].result == "clear"


async def test_recently_screened_vendor_is_not_rescreened(realdb):
    """last_screened_at = now → outside the staleness window → skipped."""
    now = datetime.now(UTC)
    vid = await _seed_vendor(
        realdb, "a", name="Recently Screened Co", last_screened_at=now, screening_status="clear"
    )

    with _patch_control(realdb):
        await rescreen_vendors_once(now=now)

    # No new trail row was appended.
    checks = await _checks_for(realdb, "a", vid)
    assert checks == []


async def test_stale_vendor_is_rescreened(realdb):
    """last_screened_at older than vendor_rescreen_after_days → due → screened."""
    now = datetime.now(UTC)
    stale = now - timedelta(days=settings.vendor_rescreen_after_days + 1)
    vid = await _seed_vendor(
        realdb, "a", name="Stale Vendor Co", last_screened_at=stale, screening_status="clear"
    )

    with _patch_control(realdb):
        await rescreen_vendors_once(now=now)

    vendor = await _get_vendor(realdb, "a", vid)
    assert vendor.last_screened_at is not None
    assert vendor.last_screened_at > stale
    checks = await _checks_for(realdb, "a", vid)
    assert len(checks) == 1
    assert checks[0].check_type == "periodic"


async def test_blocklisted_vendor_flips_to_match_and_blocks_payments(realdb):
    """A never-screened vendor whose name hits the mock SDN blocklist flips to
    match + payments_blocked, and counts as a new flag."""
    vid = await _seed_vendor(realdb, "a", name="Sanctioned Test Entity")

    with _patch_control(realdb):
        result = await rescreen_vendors_once()

    assert result.new_flags >= 1
    vendor = await _get_vendor(realdb, "a", vid)
    assert vendor.screening_status == "match"
    assert vendor.payments_blocked is True
    assert vendor.payments_blocked_at is not None
    checks = await _checks_for(realdb, "a", vid)
    assert len(checks) == 1
    assert checks[0].result == "match"
    assert checks[0].check_type == "periodic"


async def test_inactive_vendor_is_not_screened(realdb):
    """Only active vendors are re-screened — a rejected/inactive vendor is left
    alone even if it has never been screened."""
    vid = await _seed_vendor(realdb, "a", name="Inactive Vendor Co", status="inactive")

    with _patch_control(realdb):
        await rescreen_vendors_once()

    vendor = await _get_vendor(realdb, "a", vid)
    assert vendor.last_screened_at is None
    assert await _checks_for(realdb, "a", vid) == []
