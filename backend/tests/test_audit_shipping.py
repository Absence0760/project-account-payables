"""Tests for the centralized audit-log shipper + adapters.

DB-free: per-tenant shipping is mocked, the control-session iteration is
stubbed, and boto3 is patched for the CloudWatch / S3 adapters. The
DB-touching path (`_ship_tenant`) is exercised against a real Postgres
in the backend smoke stack.
"""

from __future__ import annotations

import gzip
import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.audit_shipping import (
    AuditLogRow,
    get_audit_shipping_adapter,
    list_available_providers,
    mock_adapter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(*, tenant_db: str = "ap_acme", action: str = "invoice.approved") -> AuditLogRow:
    return AuditLogRow(
        id=uuid.uuid4(),
        tenant_db=tenant_db,
        organization_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        actor_id=uuid.uuid4(),
        action=action,
        entity_type="invoice",
        entity_id=uuid.uuid4(),
        details={"field": "value"},
        created_at=datetime(2026, 4, 21, 12, 0, 0, tzinfo=UTC),
    )


def _fake_control_session(tenant_db_names: list[str]):
    """Async CM yielding a session whose `execute` returns fake org rows."""
    fake_rows = [(uuid.uuid4(), n) for n in tenant_db_names]
    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=MagicMock(all=lambda: fake_rows))

    cm = AsyncMock()
    cm.__aenter__.return_value = fake_session
    cm.__aexit__.return_value = None
    return MagicMock(return_value=cm)


# ---------------------------------------------------------------------------
# Mock adapter + registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_adapter_captures_rows():
    mock_adapter.reset()
    adapter = get_audit_shipping_adapter({"provider": "mock"})
    rows = [_row(), _row(action="invoice.rejected")]

    await adapter.ship(rows)

    assert len(mock_adapter.received) == 2
    assert mock_adapter.received[0].action == "invoice.approved"
    assert await adapter.test_connection() is True


def test_registry_lists_all_builtin_providers():
    # Importing the package side-effect-registers all built-ins.
    providers = list_available_providers()
    assert {"mock", "cloudwatch", "s3_objectlock"}.issubset(providers)


def test_dispatcher_falls_back_to_mock_for_unknown_provider():
    adapter = get_audit_shipping_adapter({"provider": "nonsense"})
    assert adapter.provider_name == "mock"


# ---------------------------------------------------------------------------
# Shipper: batch query + mark-shipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ship_once_iterates_every_tenant():
    from app.services import audit_log_shipper

    mock_adapter.reset()

    with (
        patch.object(
            audit_log_shipper,
            "control_session_factory",
            _fake_control_session(["ap_a", "ap_b", "ap_c"]),
        ),
        patch.object(
            audit_log_shipper,
            "_ship_tenant",
            AsyncMock(return_value=7),
        ) as ship_tenant,
        patch.object(
            audit_log_shipper,
            "_build_adapters",
            return_value=[mock_adapter.MockAdapter({})],
        ),
    ):
        result = await audit_log_shipper.ship_once()

    assert result.tenants_scanned == 3
    assert result.rows_shipped == 21  # 3 tenants × 7 rows
    assert result.failures == 0
    assert ship_tenant.await_count == 3


@pytest.mark.asyncio
async def test_ship_once_continues_after_one_tenant_fails():
    """Partial failures don't halt the sweep — bad tenant's rows stay
    unshipped and next tick retries, the other tenants still make progress."""
    from app.services import audit_log_shipper

    side_effects = [4, RuntimeError("connection refused"), 2]
    with (
        patch.object(
            audit_log_shipper,
            "control_session_factory",
            _fake_control_session(["ap_a", "ap_b", "ap_c"]),
        ),
        patch.object(
            audit_log_shipper,
            "_ship_tenant",
            AsyncMock(side_effect=side_effects),
        ),
        patch.object(
            audit_log_shipper,
            "_build_adapters",
            return_value=[mock_adapter.MockAdapter({})],
        ),
    ):
        result = await audit_log_shipper.ship_once()

    assert result.tenants_scanned == 3
    assert result.rows_shipped == 6  # 4 + (skipped) + 2
    assert result.failures == 1


@pytest.mark.asyncio
async def test_ship_once_noop_when_no_providers_configured():
    """No adapters = nothing to ship to, loop short-circuits without
    reading tenants. Flip `providers` at runtime is supported."""
    from app.services import audit_log_shipper

    ctrl_mock = _fake_control_session(["ap_a"])
    with (
        patch.object(audit_log_shipper, "control_session_factory", ctrl_mock),
        patch.object(audit_log_shipper, "_build_adapters", return_value=[]),
        patch.object(audit_log_shipper, "_ship_tenant", AsyncMock(return_value=5)) as ship_tenant,
    ):
        result = await audit_log_shipper.ship_once()

    assert result.tenants_scanned == 0
    assert result.rows_shipped == 0
    assert ship_tenant.await_count == 0


# ---------------------------------------------------------------------------
# Shipper: fan-out semantics (all-or-nothing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ship_tenant_requires_every_adapter_to_succeed(tmp_path):
    """If any adapter raises, `_ship_tenant` raises (caller won't mark
    shipped_at). This is the retry hook — next tick re-picks the batch."""
    from app.services import audit_log_shipper

    good = mock_adapter.MockAdapter({})
    bad = mock_adapter.MockAdapter({})
    bad.ship = AsyncMock(side_effect=RuntimeError("cloudwatch down"))

    # Fake the tenant DB query — return one row, then expect that
    # _ship_tenant raises before reaching the UPDATE.
    fake_row = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        correlation_id=None,
        actor_id=None,
        action="x",
        entity_type="y",
        entity_id=None,
        details=None,
        created_at=datetime.now(UTC),
    )

    db_session = MagicMock()
    db_session.execute = AsyncMock(
        return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [fake_row]))
    )
    db_session.commit = AsyncMock()

    cm = AsyncMock()
    cm.__aenter__.return_value = db_session
    cm.__aexit__.return_value = None

    factory = MagicMock(return_value=cm)
    engine = MagicMock()
    engine.dispose = AsyncMock()

    mock_adapter.reset()
    with (
        patch.object(audit_log_shipper, "create_async_engine", return_value=engine),
        patch.object(audit_log_shipper, "async_sessionmaker", return_value=factory),
    ):
        with pytest.raises(RuntimeError, match="cloudwatch down"):
            await audit_log_shipper._ship_tenant("ap_a", [good, bad])

    # The good adapter DID ship (no way to roll back a side-effectful
    # adapter) — documented at-least-once semantic. But crucially the
    # DB UPDATE was NOT committed, so the next tick will retry all rows.
    assert len(mock_adapter.received) == 1
    db_session.commit.assert_not_called()


@pytest.mark.asyncio
async def test_ship_tenant_marks_rows_shipped_on_success():
    from app.services import audit_log_shipper

    fake_row = SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        correlation_id=None,
        actor_id=None,
        action="x",
        entity_type="y",
        entity_id=None,
        details=None,
        created_at=datetime.now(UTC),
    )

    db_session = MagicMock()
    # First execute() returns the SELECT result; second (the UPDATE) returns
    # a plain mock.
    db_session.execute = AsyncMock(
        side_effect=[
            MagicMock(scalars=lambda: MagicMock(all=lambda: [fake_row])),
            MagicMock(),
        ]
    )
    db_session.commit = AsyncMock()

    cm = AsyncMock()
    cm.__aenter__.return_value = db_session
    cm.__aexit__.return_value = None

    factory = MagicMock(return_value=cm)
    engine = MagicMock()
    engine.dispose = AsyncMock()

    mock_adapter.reset()
    good = mock_adapter.MockAdapter({})

    with (
        patch.object(audit_log_shipper, "create_async_engine", return_value=engine),
        patch.object(audit_log_shipper, "async_sessionmaker", return_value=factory),
    ):
        shipped = await audit_log_shipper._ship_tenant("ap_a", [good])

    assert shipped == 1
    db_session.commit.assert_awaited_once()
    # Two execute calls: SELECT then UPDATE.
    assert db_session.execute.await_count == 2


# ---------------------------------------------------------------------------
# Disabled / lifespan-flag behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_does_not_start_shipper_when_disabled(monkeypatch):
    """Default `audit_shipping_enabled=False` means no shipper task is
    created. Guards against accidentally firing AWS calls in local dev."""

    import app.main as main_mod

    # Lifespan now refuses to boot in non-debug mode when AP_SECRET_KEY is at
    # its insecure default. The test inherits the dev defaults, so flip the
    # debug bypass on for this lifespan exercise.
    monkeypatch.setattr(main_mod.settings, "debug", True)
    monkeypatch.setattr(main_mod.settings, "extraction_reaper_enabled", False)
    monkeypatch.setattr(main_mod.settings, "audit_shipping_enabled", False)

    created_tasks: list[str] = []

    def spy_create_task(coro, **kwargs):
        name = kwargs.get("name") or ""
        created_tasks.append(name)
        # Cancel the coroutine so it doesn't actually run.
        coro.close()
        task = MagicMock()
        task.cancel = MagicMock()

        async def _await():
            return None

        task.__await__ = lambda: _await().__await__()
        return task

    with patch("asyncio.create_task", side_effect=spy_create_task):
        async with main_mod.lifespan(main_mod.app):
            pass

    assert "audit-log-shipper" not in created_tasks
    assert "extraction-reaper" not in created_tasks


# ---------------------------------------------------------------------------
# Loop: happy path + resilience
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_shipper_loop_cancels_cleanly():
    import asyncio

    from app.services import audit_log_shipper

    with patch.object(audit_log_shipper, "ship_once", AsyncMock(return_value=SimpleNamespace())):
        task = asyncio.create_task(audit_log_shipper.run_shipper_loop())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_run_shipper_loop_survives_a_failed_sweep():
    """One sweep raising must not kill the loop."""
    import asyncio

    from app.services import audit_log_shipper

    call_count = 0

    async def flaky():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        return SimpleNamespace()

    with (
        patch.object(audit_log_shipper, "ship_once", flaky),
        patch.object(audit_log_shipper.settings, "audit_shipping_interval_seconds", 0.01),
    ):
        task = asyncio.create_task(audit_log_shipper.run_shipper_loop())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert call_count >= 2


# ---------------------------------------------------------------------------
# CloudWatch adapter — boto3 mocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloudwatch_adapter_groups_rows_by_tenant_and_day():
    from app.services.audit_shipping import cloudwatch_adapter as cw_mod

    fake_client = MagicMock()
    fake_client.exceptions.ResourceAlreadyExistsException = type(
        "FakeAlreadyExists", (Exception,), {}
    )
    with patch("boto3.client", return_value=fake_client):
        adapter = cw_mod.CloudWatchAdapter({})

    rows = [
        _row(tenant_db="ap_a"),
        _row(tenant_db="ap_a", action="invoice.rejected"),
        _row(tenant_db="ap_b"),
    ]
    await adapter.ship(rows)

    # Two different streams (ap_a/2026-04-21, ap_b/2026-04-21) → two put_log_events calls.
    assert fake_client.put_log_events.call_count == 2
    stream_names = {
        call.kwargs["logStreamName"] for call in fake_client.put_log_events.call_args_list
    }
    assert stream_names == {"ap_a/2026-04-21", "ap_b/2026-04-21"}


@pytest.mark.asyncio
async def test_cloudwatch_test_connection_returns_bool():
    from app.services.audit_shipping import cloudwatch_adapter as cw_mod

    fake_client = MagicMock()
    fake_client.exceptions.ResourceAlreadyExistsException = type(
        "FakeAlreadyExists", (Exception,), {}
    )

    with patch("boto3.client", return_value=fake_client):
        adapter = cw_mod.CloudWatchAdapter({})

    assert await adapter.test_connection() is True


# ---------------------------------------------------------------------------
# S3 Object Lock adapter — boto3 mocked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_s3_objectlock_writes_gzipped_jsonl():
    from app.services.audit_shipping import s3_objectlock_adapter as s3_mod

    fake_client = MagicMock()
    with patch("boto3.client", return_value=fake_client):
        adapter = s3_mod.S3ObjectLockAdapter({"bucket_name": "audit-bucket"})

    rows = [_row(tenant_db="ap_a"), _row(tenant_db="ap_a", action="invoice.rejected")]
    await adapter.ship(rows)

    fake_client.put_object.assert_called_once()
    kwargs = fake_client.put_object.call_args.kwargs
    assert kwargs["Bucket"] == "audit-bucket"
    assert kwargs["ContentEncoding"] == "gzip"
    assert kwargs["Key"].startswith("audit/ap_a/2026/04/21/")
    assert kwargs["Key"].endswith(".jsonl.gz")

    # Body decodes back to two JSONL records.
    body = gzip.decompress(kwargs["Body"]).decode("utf-8").splitlines()
    assert len(body) == 2
    assert json.loads(body[0])["action"] == "invoice.approved"


def test_s3_objectlock_requires_bucket():
    from app.services.audit_shipping import s3_objectlock_adapter as s3_mod

    with patch("boto3.client"), patch.object(s3_mod.settings, "audit_shipping_s3_bucket", None):
        with pytest.raises(ValueError, match="AP_AUDIT_SHIPPING_S3_BUCKET"):
            s3_mod.S3ObjectLockAdapter({})


@pytest.mark.asyncio
async def test_s3_objectlock_test_connection_fails_without_object_lock():
    """Object Lock must be enabled at bucket creation. An ordinary bucket
    should fail the startup self-test."""
    from botocore.exceptions import ClientError

    from app.services.audit_shipping import s3_objectlock_adapter as s3_mod

    fake_client = MagicMock()
    fake_client.get_object_lock_configuration.side_effect = ClientError(
        {"Error": {"Code": "ObjectLockConfigurationNotFoundError"}}, "GetObjectLock"
    )

    with patch("boto3.client", return_value=fake_client):
        adapter = s3_mod.S3ObjectLockAdapter({"bucket_name": "plain-bucket"})

    assert await adapter.test_connection() is False
