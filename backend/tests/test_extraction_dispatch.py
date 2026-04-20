"""Tests for the extraction dispatch worker pool.

DB-free: we mock the DB and extraction internals so we can assert on
queue/worker orchestration and failure handling without a live Postgres.
The _run_local end-to-end path is excluded — it requires real DB engines
and is covered by the integration suite.
"""

from __future__ import annotations

import queue
import threading
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_module():
    """Re-import extraction_dispatch with a cleared module state.

    The module holds process-global state (_job_queue, _worker_threads).
    We patch the module-level names so each test starts clean without
    actually reimporting.
    """
    import app.services.extraction_dispatch as mod

    return mod


def _drain_workers(mod, timeout: float = 1.0):
    """Wait for all worker threads in the module to finish."""
    for t in list(mod._worker_threads):
        t.join(timeout=timeout)


# ---------------------------------------------------------------------------
# _ensure_workers
# ---------------------------------------------------------------------------


def test_ensure_workers_starts_worker_count_threads():
    """Starting from zero threads, _ensure_workers creates exactly _WORKER_COUNT."""
    import app.services.extraction_dispatch as mod

    original_threads = list(mod._worker_threads)
    mod._worker_threads.clear()

    with patch("threading.Thread") as mock_thread_cls:
        mock_instances = []
        for _ in range(mod._WORKER_COUNT):
            t = MagicMock()
            t.is_alive.return_value = True
            mock_instances.append(t)
        mock_thread_cls.side_effect = mock_instances

        mod._ensure_workers()

    assert mock_thread_cls.call_count == mod._WORKER_COUNT
    for t in mock_instances:
        t.start.assert_called_once()

    # Restore
    mod._worker_threads[:] = original_threads


def test_ensure_workers_does_not_over_create_when_already_at_capacity():
    """If _WORKER_COUNT live threads already exist, _ensure_workers is a no-op."""
    import app.services.extraction_dispatch as mod

    original_threads = list(mod._worker_threads)

    fake_threads = []
    for _ in range(mod._WORKER_COUNT):
        t = MagicMock(spec=threading.Thread)
        t.is_alive.return_value = True
        fake_threads.append(t)

    mod._worker_threads[:] = fake_threads

    with patch("threading.Thread") as mock_thread_cls:
        mod._ensure_workers()
        mock_thread_cls.assert_not_called()

    # Restore
    mod._worker_threads[:] = original_threads


def test_ensure_workers_replaces_dead_threads():
    """Dead threads are pruned and new ones started to top back up to _WORKER_COUNT."""
    import app.services.extraction_dispatch as mod

    original_threads = list(mod._worker_threads)

    dead_thread = MagicMock()
    dead_thread.is_alive.return_value = False
    mod._worker_threads[:] = [dead_thread] * mod._WORKER_COUNT  # all dead

    with patch("threading.Thread") as mock_thread_cls:
        new_threads = []
        for _ in range(mod._WORKER_COUNT):
            t = MagicMock()
            t.is_alive.return_value = True
            new_threads.append(t)
        mock_thread_cls.side_effect = new_threads

        mod._ensure_workers()

    assert mock_thread_cls.call_count == mod._WORKER_COUNT

    # Restore
    mod._worker_threads[:] = original_threads


def test_ensure_workers_is_thread_safe():
    """Concurrent calls must not create more than _WORKER_COUNT threads."""
    import app.services.extraction_dispatch as mod

    original_threads = list(mod._worker_threads)
    mod._worker_threads.clear()

    started: list[MagicMock] = []
    lock = threading.Lock()

    # Keep a real reference to threading.Thread before patching
    RealThread = threading.Thread

    def make_thread(**kwargs):
        t = MagicMock()
        t.is_alive.return_value = True

        def start():
            with lock:
                started.append(t)

        t.start.side_effect = start
        return t

    with patch("threading.Thread", side_effect=make_thread):
        # Use the real Thread for the callers, not the patched one
        threads_calling = []
        for _ in range(6):
            caller = RealThread(target=mod._ensure_workers)
            threads_calling.append(caller)
        for c in threads_calling:
            c.start()
        for c in threads_calling:
            c.join()

    assert len(started) == mod._WORKER_COUNT

    # Restore
    mod._worker_threads[:] = original_threads


# ---------------------------------------------------------------------------
# dispatch_extraction — local mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_extraction_local_puts_job_on_queue():
    """In local mode, dispatch_extraction enqueues a (invoice_id, org_id, actor_id) tuple."""
    import app.services.extraction_dispatch as mod

    invoice_id = uuid.uuid4()
    org_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    with (
        patch.object(mod.settings, "extraction_mode", "local"),
        patch.object(mod, "_ensure_workers") as mock_ensure,
    ):
        original_qsize = mod._job_queue.qsize()
        await mod.dispatch_extraction(invoice_id, org_id, actor_id)
        assert mod._job_queue.qsize() == original_qsize + 1

        item = mod._job_queue.get_nowait()
        assert item == (invoice_id, org_id, actor_id)

        mock_ensure.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_extraction_local_calls_ensure_workers():
    """dispatch_extraction starts workers after enqueuing the job."""
    import app.services.extraction_dispatch as mod

    with (
        patch.object(mod.settings, "extraction_mode", "local"),
        patch.object(mod, "_ensure_workers") as mock_ensure,
    ):
        await mod.dispatch_extraction(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
        # Drain the queue item we just added
        mod._job_queue.get_nowait()
        mock_ensure.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_extraction_lambda_calls_sqs_not_queue():
    """In lambda mode, dispatch_extraction sends to SQS and never touches the queue."""
    import app.services.extraction_dispatch as mod

    with (
        patch.object(mod.settings, "extraction_mode", "lambda"),
        patch.object(mod, "_send_to_sqs") as mock_sqs,
        patch.object(mod, "_ensure_workers") as mock_ensure,
    ):
        inv_id = uuid.uuid4()
        org_id = uuid.uuid4()
        actor_id = uuid.uuid4()
        original_qsize = mod._job_queue.qsize()

        await mod.dispatch_extraction(inv_id, org_id, actor_id)

        mock_sqs.assert_called_once_with(inv_id, org_id, actor_id)
        mock_ensure.assert_not_called()
        assert mod._job_queue.qsize() == original_qsize


# ---------------------------------------------------------------------------
# _fail_invoice_safely
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_invoice_safely_transitions_pending_to_failed():
    """A pending invoice is transitioned to failed and the session committed."""
    import app.services.extraction_dispatch as mod
    from app.models.invoice import InvoiceStatus

    invoice_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    fake_invoice = SimpleNamespace(
        id=invoice_id,
        status=InvoiceStatus.pending,
    )

    db = AsyncMock()
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=fake_invoice)

    await mod._fail_invoice_safely(db, invoice_id, actor_id, "boom")

    assert fake_invoice.status == InvoiceStatus.failed
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_fail_invoice_safely_is_noop_for_non_pending_invoice():
    """An invoice that is not in pending (e.g. already failed) is left unchanged."""
    import app.services.extraction_dispatch as mod
    from app.models.invoice import InvoiceStatus

    invoice_id = uuid.uuid4()

    fake_invoice = SimpleNamespace(
        id=invoice_id,
        status=InvoiceStatus.failed,
    )

    db = AsyncMock()
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=fake_invoice)

    await mod._fail_invoice_safely(db, invoice_id, None, "boom")

    assert fake_invoice.status == InvoiceStatus.failed
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_invoice_safely_is_noop_when_invoice_not_found():
    """A missing invoice (already cleaned up) does not raise."""
    import app.services.extraction_dispatch as mod

    db = AsyncMock()
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)

    # Must not raise — the caller's cleanup path must not be disrupted.
    await mod._fail_invoice_safely(db, uuid.uuid4(), None, "boom")

    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_fail_invoice_safely_swallows_db_errors():
    """If the DB itself errors, _fail_invoice_safely swallows it."""
    import app.services.extraction_dispatch as mod

    db = AsyncMock()
    db.execute.side_effect = RuntimeError("connection error")

    # Must not raise
    await mod._fail_invoice_safely(db, uuid.uuid4(), None, "boom")


# ---------------------------------------------------------------------------
# _mark_failed
# ---------------------------------------------------------------------------


def _make_session_cm(session):
    """Wrap an AsyncMock session in an async context manager."""
    cm = AsyncMock()
    cm.__aenter__.return_value = session
    cm.__aexit__.return_value = None
    return cm


def _make_async_session(scalar_return_value):
    """Build a session whose execute().scalar_one_or_none() returns a real value
    (not a coroutine). An AsyncMock's auto-attribute .return_value can spawn
    further AsyncMocks whose .scalar_one_or_none() becomes awaitable; we avoid
    that by explicitly typing the execute return_value as a plain MagicMock."""
    execute_result = MagicMock()
    execute_result.scalar_one_or_none = MagicMock(return_value=scalar_return_value)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)
    return session


@pytest.mark.asyncio
async def test_mark_failed_transitions_pending_invoice_to_failed():
    """_mark_failed creates fresh engines, finds the invoice, and sets status=failed."""
    import app.services.extraction_dispatch as mod
    from app.models.invoice import InvoiceStatus

    invoice_id = uuid.uuid4()
    org_id = uuid.uuid4()

    fake_org = SimpleNamespace(id=org_id, db_name="ap_test")
    fake_invoice = SimpleNamespace(id=invoice_id, status=InvoiceStatus.pending)

    ctrl_session = _make_async_session(fake_org)
    tenant_session = _make_async_session(fake_invoice)

    mock_ctrl_engine = AsyncMock()
    mock_tenant_engine = AsyncMock()

    engine_call_count = 0

    def make_engine(url, **kwargs):
        nonlocal engine_call_count
        engine_call_count += 1
        if engine_call_count == 1:
            return mock_ctrl_engine
        return mock_tenant_engine

    def make_factory(engine, **kwargs):
        if engine is mock_ctrl_engine:
            return MagicMock(return_value=_make_session_cm(ctrl_session))
        return MagicMock(return_value=_make_session_cm(tenant_session))

    with (
        patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=make_engine),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", side_effect=make_factory),
        patch(
            "app.database._make_tenant_url",
            return_value="postgresql+asyncpg://localhost/ap_test",
        ),
    ):
        await mod._mark_failed(invoice_id, org_id, "extraction crashed")

    assert fake_invoice.status == InvoiceStatus.failed
    tenant_session.commit.assert_awaited_once()
    mock_ctrl_engine.dispose.assert_awaited()
    mock_tenant_engine.dispose.assert_awaited()


@pytest.mark.asyncio
async def test_mark_failed_is_noop_when_org_not_found():
    """If the org has been deleted, _mark_failed returns without touching a tenant DB."""
    import app.services.extraction_dispatch as mod

    ctrl_session = _make_async_session(None)

    mock_ctrl_engine = AsyncMock()
    mock_tenant_engine = AsyncMock()

    engine_call_count = 0

    def make_engine(url, **kwargs):
        nonlocal engine_call_count
        engine_call_count += 1
        return mock_ctrl_engine if engine_call_count == 1 else mock_tenant_engine

    def make_factory(engine, **kwargs):
        return MagicMock(return_value=_make_session_cm(ctrl_session))

    with (
        patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=make_engine),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", side_effect=make_factory),
    ):
        # Must not raise
        await mod._mark_failed(uuid.uuid4(), uuid.uuid4(), "reason")

    # ctrl engine created and disposed; tenant engine never created
    mock_ctrl_engine.dispose.assert_awaited()
    mock_tenant_engine.dispose.assert_not_awaited()


@pytest.mark.asyncio
async def test_mark_failed_does_not_transition_non_pending_invoice():
    """An invoice already in failed/done state is left unchanged."""
    import app.services.extraction_dispatch as mod
    from app.models.invoice import InvoiceStatus

    invoice_id = uuid.uuid4()
    org_id = uuid.uuid4()

    fake_org = SimpleNamespace(id=org_id, db_name="ap_test")
    fake_invoice = SimpleNamespace(id=invoice_id, status=InvoiceStatus.done)

    ctrl_session = _make_async_session(fake_org)
    tenant_session = _make_async_session(fake_invoice)

    mock_ctrl_engine = AsyncMock()
    mock_tenant_engine = AsyncMock()

    engine_call_count = 0

    def make_engine(url, **kwargs):
        nonlocal engine_call_count
        engine_call_count += 1
        if engine_call_count == 1:
            return mock_ctrl_engine
        return mock_tenant_engine

    def make_factory(engine, **kwargs):
        if engine is mock_ctrl_engine:
            return MagicMock(return_value=_make_session_cm(ctrl_session))
        return MagicMock(return_value=_make_session_cm(tenant_session))

    with (
        patch("sqlalchemy.ext.asyncio.create_async_engine", side_effect=make_engine),
        patch("sqlalchemy.ext.asyncio.async_sessionmaker", side_effect=make_factory),
        patch(
            "app.database._make_tenant_url",
            return_value="postgresql+asyncpg://localhost/ap_test",
        ),
    ):
        await mod._mark_failed(invoice_id, org_id, "reason")

    assert fake_invoice.status == InvoiceStatus.done
    tenant_session.commit.assert_not_awaited()
