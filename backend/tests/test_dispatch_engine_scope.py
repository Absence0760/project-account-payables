"""The loop-safety chokepoint for dispatchers that run on their own event loop.

`app.database` holds module-level engines bound to the app's event loop. An
asyncpg connection cannot cross loops: using one from a second loop raises
`RuntimeError: got Future attached to a different loop` AND can return the
half-used connection to the pool the REQUEST path draws from, after which
unrelated requests hang on it. That is not theoretical — it took nine e2e specs
red via `payment_erp_sync`, presenting as `PATCH /api/organization` timing out.

`extraction_dispatch` cannot simply move to the app loop the way
`erp_dispatch` and `payment_erp_sync` did: it runs PyMuPDF rendering and
Tesseract OSD, synchronous CPU work that would stall the request loop. So it
keeps its worker threads and declares loop-local engines with
`dispatch_engine_scope` instead.

**The point of these tests is the indirection, not the plumbing.** The code
that actually broke — `notification_dispatch`, `audit_dispatch`,
`webhooks.dispatch` — is reached from `transition_invoice`, is never called
directly by a dispatcher, and cannot be handed a session. It reaches for the
module-level global. So the tests that matter assert those modules pick up the
ambient engines *without knowing they exist*.

Pure Python: no DB, no network. Engines are created but never connected
(`create_async_engine` is lazy).
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from unittest.mock import patch

import pytest

from app import database


class _FakeSession:
    """Async-context-manager stand-in that records that it was used."""

    def __init__(self, marker: str, log: list[str]):
        self.marker = marker
        self._log = log

    async def __aenter__(self):
        self._log.append(self.marker)
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_a, **_k):
        class _Result:
            def scalars(self_inner):
                return self_inner

            def all(self_inner):
                return []

            def scalar_one_or_none(self_inner):
                return None

        return _Result()


def _fake_sessionmaker(marker: str, log: list[str]):
    return lambda: _FakeSession(marker, log)


# ---------------------------------------------------------------------------
# The control-plane indirection
# ---------------------------------------------------------------------------


async def test_control_session_factory_uses_the_default_outside_a_scope():
    """No scope bound → the app-loop sessionmaker, i.e. today's behaviour for
    every request. The indirection must be invisible on the normal path."""
    log: list[str] = []
    with patch.object(database, "_default_control_session_factory", _fake_sessionmaker("app", log)):
        async with database.control_session_factory():
            pass
    assert log == ["app"]


async def test_control_session_factory_uses_the_ambient_sessionmaker_inside_a_scope():
    log: list[str] = []
    with patch.object(database, "_default_control_session_factory", _fake_sessionmaker("app", log)):
        async with database.dispatch_engine_scope(
            control_sessionmaker=_fake_sessionmaker("worker", log)
        ):
            async with database.control_session_factory():
                pass
        # …and the binding is undone on exit, not left dangling for the next
        # thing this loop runs.
        async with database.control_session_factory():
            pass
    assert log == ["worker", "app"]


async def test_notification_dispatch_picks_up_the_ambient_sessionmaker():
    """The whole point: `notification_dispatch._load_recipients` opens its own
    control-plane session by reaching for the module global. It is reached from
    `transition_invoice`, so a dispatcher cannot pass it anything — and it is
    exactly where the live `RuntimeError` was raised. It must resolve to the
    worker's engine without being edited."""
    from app.services import notification_dispatch

    log: list[str] = []
    with patch.object(database, "_default_control_session_factory", _fake_sessionmaker("app", log)):
        async with database.dispatch_engine_scope(
            control_sessionmaker=_fake_sessionmaker("worker", log)
        ):
            await notification_dispatch._load_recipients([uuid.uuid4()])
    assert log == ["worker"], "the notification hook still used the app-loop engine"


async def test_audit_dispatch_picks_up_the_ambient_sessionmaker():
    """`audit_dispatch._resolve_tenant_db_name` is the same shape, on the audit
    path rather than the notification one."""
    from app.services import audit_dispatch

    log: list[str] = []
    with patch.object(database, "_default_control_session_factory", _fake_sessionmaker("app", log)):
        async with database.dispatch_engine_scope(
            control_sessionmaker=_fake_sessionmaker("worker", log)
        ):
            with pytest.raises(ValueError):
                # No org row from the fake → raises, which is fine: we are
                # asserting WHICH engine it asked, not what it found.
                await audit_dispatch._resolve_tenant_db_name(uuid.uuid4())
    assert log == ["worker"], "the audit hook still used the app-loop engine"


# ---------------------------------------------------------------------------
# The tenant-engine indirection
# ---------------------------------------------------------------------------


async def test_tenant_engine_resolves_to_a_seeded_scope_engine():
    """A seeded engine is reused rather than a second pool being opened for the
    same database — `dispatch_audit` asks for the tenant the worker already has
    open."""
    engine = database.create_async_engine(database._make_tenant_url("feoh_scope_seed"))
    try:
        async with database.dispatch_engine_scope(
            control_sessionmaker=_fake_sessionmaker("worker", []),
            tenant_engines={"feoh_scope_seed": engine},
        ):
            assert database.get_tenant_engine("feoh_scope_seed") is engine
        assert "feoh_scope_seed" not in database._tenant_engines, (
            "a scope-resolved tenant engine leaked into the app-loop cache"
        )
    finally:
        await engine.dispose()


async def test_scope_disposes_engines_it_created_but_not_borrowed_ones():
    """Owned vs borrowed. A worker runs many jobs; an engine created per job and
    never disposed is a leaked pool each time. A borrowed one must survive,
    because the caller is still using it."""
    borrowed = database.create_async_engine(database._make_tenant_url("feoh_scope_borrowed"))
    borrowed_pool_before = borrowed.pool
    try:
        async with database.dispatch_engine_scope(
            control_sessionmaker=_fake_sessionmaker("worker", []),
            tenant_engines={"feoh_scope_borrowed": borrowed},
        ):
            made = database.get_tenant_engine("feoh_scope_made")
            assert made is not borrowed
            made_pool_before = made.pool

        # `dispose()` REPLACES the engine's pool, so pool identity is the
        # observable proof — asserting the attribute merely exists would pass
        # whether or not anything was disposed.
        assert made.pool is not made_pool_before, (
            "a tenant engine the scope created was not disposed — a worker "
            "running many jobs would leak one pool per job"
        )
        assert borrowed.pool is borrowed_pool_before, (
            "the scope disposed an engine it borrowed — the caller is still "
            "using that one for its own work"
        )
        assert "feoh_scope_borrowed" not in database._tenant_engines
    finally:
        await borrowed.dispose()


async def test_tenant_engine_uses_the_app_cache_outside_a_scope():
    """Outside a scope, unchanged: the shared, larger-pool, app-loop cache."""
    name = "feoh_scope_appcache"
    try:
        first = database.get_tenant_engine(name)
        assert database.get_tenant_engine(name) is first, "app-loop cache stopped caching"
        assert name in database._tenant_engines
    finally:
        eng = database._tenant_engines.pop(name, None)
        if eng is not None:
            await eng.dispose()


# ---------------------------------------------------------------------------
# Thread isolation — why a ContextVar, and not a module-level global
# ---------------------------------------------------------------------------


async def test_a_worker_threads_scope_never_leaks_into_the_request_context():
    """The safety property that makes this approach viable at all.

    A new thread starts with an EMPTY context, so a worker binding its engines
    cannot make a concurrent request resolve to them — which, since the
    worker's engines belong to the worker's loop, would be the same cross-loop
    bug pointed the other way.
    """
    log: list[str] = []
    seen_in_thread: list[str | None] = []

    def _worker():
        async def _inner():
            async with database.dispatch_engine_scope(
                control_sessionmaker=_fake_sessionmaker("worker", log)
            ):
                async with database.control_session_factory():
                    pass
                seen_in_thread.append("bound")

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_inner())
        finally:
            loop.close()

    with patch.object(database, "_default_control_session_factory", _fake_sessionmaker("app", log)):
        t = threading.Thread(target=_worker)
        t.start()
        t.join(timeout=10)
        assert seen_in_thread == ["bound"], "worker scope never ran"
        # Back on this loop, with the worker's scope still "open" in its own
        # thread as far as wall-clock is concerned: we must still get the app
        # engine.
        async with database.control_session_factory():
            pass

    assert log == ["worker", "app"]


async def test_scope_survives_a_raise_and_still_unbinds():
    """A dispatcher whose job raises must not leave the binding set — the next
    job on that thread's loop would inherit engines from a closed one."""
    log: list[str] = []
    with patch.object(database, "_default_control_session_factory", _fake_sessionmaker("app", log)):
        with pytest.raises(RuntimeError):
            async with database.dispatch_engine_scope(
                control_sessionmaker=_fake_sessionmaker("worker", log)
            ):
                raise RuntimeError("job blew up")
        async with database.control_session_factory():
            pass
    assert log == ["app"], "binding survived an exception"


# ---------------------------------------------------------------------------
# Work that would OUTLIVE the scope
# ---------------------------------------------------------------------------


async def test_in_dispatch_scope_reports_the_binding():
    assert database.in_dispatch_scope() is False
    async with database.dispatch_engine_scope(
        control_sessionmaker=_fake_sessionmaker("worker", [])
    ):
        assert database.in_dispatch_scope() is True
    assert database.in_dispatch_scope() is False


async def test_webhook_immediate_attempt_is_skipped_inside_a_scope():
    """`emit_event` fire-and-forgets an immediate delivery attempt, and
    `transition_invoice` calls `emit_event` — so it lands inside a worker's
    scope without either side knowing.

    `create_task` copies the context, so that task would inherit the worker's
    engines and then be abandoned when the worker's loop closes, its engines
    disposed underneath it — quite possibly mid-POST to a customer endpoint,
    with the delivery row already written. The sweep is the durable owner.
    """
    from app.services.webhooks import dispatch as wh

    spawned: list[uuid.UUID] = []

    async def _fake_process(delivery_id):
        spawned.append(delivery_id)

    with patch("app.services.webhooks.delivery.process_delivery_by_id", _fake_process, create=True):
        # Outside a scope: the immediate attempt still runs (unchanged
        # behaviour for the request path and for the task-based dispatchers).
        wh._spawn_immediate_attempt(uuid.uuid4())
        await asyncio.sleep(0)
        assert len(spawned) == 1, "immediate attempt should still run on the app loop"

        async with database.dispatch_engine_scope(
            control_sessionmaker=_fake_sessionmaker("worker", [])
        ):
            wh._spawn_immediate_attempt(uuid.uuid4())
            await asyncio.sleep(0)

    assert len(spawned) == 1, "an immediate attempt was spawned inside a dispatcher scope"
