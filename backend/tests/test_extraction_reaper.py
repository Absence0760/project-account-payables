"""Tests for the stuck-extraction reaper.

DB-free: we mock the per-tenant sweep and the control session so we can
assert on tenant iteration, threshold handling, and partial-failure
tolerance. The DB-touching path (`_reap_tenant`) is exercised against a
real Postgres in the local stack — see tests/test_api_contracts.py for
the smoke pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_control_session(tenant_db_names: list[str]):
    """Return an async context-manager that yields a fake session whose
    `execute` returns rows for the given tenant DB names."""
    fake_rows = [(f"org-{n}", n) for n in tenant_db_names]
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(all=lambda: fake_rows))

    @AsyncMock
    async def factory():
        return fake_session

    cm = AsyncMock()
    cm.__aenter__.return_value = fake_session
    cm.__aexit__.return_value = None
    return MagicMock(return_value=cm)


@pytest.mark.asyncio
async def test_reap_once_iterates_every_tenant():
    from app.services import extraction_reaper

    with (
        patch.object(
            extraction_reaper,
            "control_session_factory",
            _fake_control_session(["ap_a", "ap_b", "ap_c"]),
        ),
        patch.object(extraction_reaper, "_reap_tenant", AsyncMock(return_value=2)) as reap_tenant,
    ):
        result = await extraction_reaper.reap_once()

    assert result.tenants_scanned == 3
    assert result.invoices_reaped == 6  # 3 tenants × 2 each
    assert result.failures == 0
    assert reap_tenant.await_count == 3


@pytest.mark.asyncio
async def test_reap_once_continues_after_one_tenant_fails():
    """One bad tenant DB shouldn't halt the sweep — log + move on."""
    from app.services import extraction_reaper

    side_effects = [2, RuntimeError("connection refused"), 1]
    with (
        patch.object(
            extraction_reaper,
            "control_session_factory",
            _fake_control_session(["ap_a", "ap_b", "ap_c"]),
        ),
        patch.object(extraction_reaper, "_reap_tenant", AsyncMock(side_effect=side_effects)),
    ):
        result = await extraction_reaper.reap_once()

    assert result.tenants_scanned == 3
    assert result.invoices_reaped == 3  # 2 + (skipped) + 1
    assert result.failures == 1


@pytest.mark.asyncio
async def test_reap_once_uses_explicit_threshold_over_default():
    """CLI uses --threshold to override the configured default."""
    from app.services import extraction_reaper

    captured: dict = {}

    async def capture(db_name, cutoff):
        captured["cutoff"] = cutoff
        return 0

    with (
        patch.object(extraction_reaper, "control_session_factory", _fake_control_session(["ap_a"])),
        patch.object(extraction_reaper, "_reap_tenant", capture),
    ):
        before = datetime.now(UTC)
        await extraction_reaper.reap_once(threshold_seconds=10)
        after = datetime.now(UTC)

    # Cutoff = now - 10s. Allow generous slack for test scheduling.
    assert before - timedelta(seconds=15) <= captured["cutoff"] <= after - timedelta(seconds=5)


@pytest.mark.asyncio
async def test_run_reaper_loop_cancels_cleanly():
    """Server shutdown cancels the loop task — must not raise."""
    import asyncio

    from app.services import extraction_reaper

    with patch.object(extraction_reaper, "reap_once", AsyncMock(return_value=SimpleNamespace())):
        task = asyncio.create_task(extraction_reaper.run_reaper_loop())
        await asyncio.sleep(0.05)  # let one iteration kick off
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_run_reaper_loop_survives_a_failed_sweep():
    """A raise inside reap_once must not kill the long-lived loop —
    otherwise one bad event silences the reaper for the lifetime of the
    process."""
    import asyncio

    from app.services import extraction_reaper

    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return SimpleNamespace()

    with (
        patch.object(extraction_reaper, "reap_once", flaky),
        patch.object(extraction_reaper.settings, "extraction_reaper_interval_seconds", 0.01),
    ):
        task = asyncio.create_task(extraction_reaper.run_reaper_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert call_count >= 2  # didn't die on the first raise
