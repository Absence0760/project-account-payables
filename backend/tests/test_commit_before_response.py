"""Read-after-write durability — a write is committed BEFORE its response ships.

FastAPI unwinds a ``Depends(yield)`` dependency's post-yield code from an
``AsyncExitStack`` it only exits *after* ``await response(scope, receive, send)``.
A session that commits in that teardown therefore acknowledges a write it has
not yet made durable: the client holds a ``201`` for a row a fast enough
follow-up read can miss. Measured on the real app before the fix, both the
tenant and control commits landed after ``http.response.start``.

``app.database.commit_before_response`` moves the success-path commit onto the
inner stack FastAPI unwinds *before* sending.

These tests pin the ordering invariant directly rather than racing a live
server. Two reasons that is the stronger guard: the race is only *observable*
when server/middleware pacing happens to let the client back in (it did not
reproduce over loopback here even while the defect was measurably present), and
in-process ASGI transports await the whole app call, so a create-then-read pair
can never observe it at all. Ordering is the invariant; a lost race is just one
symptom of breaking it.

See docs/decisions.md §20 and docs/known-issues.md.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException, Request

from app.database import _FUNCTION_ASTACK_KEY, commit_before_response

# ---------------------------------------------------------------------------
# Harness — records commit order against the ASGI send that ships the response
# ---------------------------------------------------------------------------

RESPONSE_START = ">>> http.response.start"


class FakeSession:
    """Minimal AsyncSession stand-in: records commits/rollbacks in order."""

    def __init__(self, events: list[str], *, in_txn: bool = True) -> None:
        self.events = events
        self._in_txn = in_txn

    def in_transaction(self) -> bool:
        return self._in_txn

    async def commit(self) -> None:
        self.events.append("commit")
        self._in_txn = False

    async def rollback(self) -> None:
        self.events.append("rollback")
        self._in_txn = False


class SendRecorder:
    """ASGI shim recording the moment response bytes are handed downstream."""

    def __init__(self, inner, events: list[str]) -> None:
        self.inner = inner
        self.events = events

    async def __call__(self, scope, receive, send):
        async def wrapped(message):
            if message["type"] == "http.response.start":
                self.events.append(RESPONSE_START)
            await send(message)

        await self.inner(scope, receive, wrapped)


def _build_app(events: list[str], *, raises: bool = False, register: bool = True):
    """A one-route app whose session provider mirrors the real ones."""
    app = FastAPI()

    async def provider(request: Request) -> AsyncGenerator[FakeSession]:
        session = FakeSession(events)
        if register:
            commit_before_response(session, request)
        try:
            yield session
            if session.in_transaction():
                await session.commit()
        except Exception:
            await session.rollback()
            raise

    @app.post("/thing")
    async def create_thing(session: FakeSession = Depends(provider)) -> dict:
        events.append("handler-write")
        if raises:
            raise HTTPException(status_code=400, detail="nope")
        return {"ok": True}

    return SendRecorder(app, events)


async def _post(app) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post("/thing")


# ---------------------------------------------------------------------------
# The invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_commit_lands_before_the_response_is_sent():
    events: list[str] = []
    resp = await _post(_build_app(events))

    assert resp.status_code == 200
    assert "commit" in events, events
    assert RESPONSE_START in events, events
    assert events.index("commit") < events.index(RESPONSE_START), (
        f"commit must precede the response being sent, got: {events}"
    )


@pytest.mark.asyncio
async def test_commit_happens_exactly_once():
    """The post-yield backstop must not double-commit an already-clean session.

    Guarding this keeps the fix from silently costing every request a second
    BEGIN/COMMIT round trip.
    """
    events: list[str] = []
    await _post(_build_app(events))

    assert events.count("commit") == 1, events


@pytest.mark.asyncio
async def test_no_commit_when_the_handler_raises():
    """An in-flight exception must reach the post-yield rollback with nothing
    committed behind its back."""
    events: list[str] = []
    resp = await _post(_build_app(events, raises=True))

    assert resp.status_code == 400
    assert "commit" not in events, events
    assert "rollback" in events, events


@pytest.mark.asyncio
async def test_backstop_still_commits_when_the_hook_cannot_register():
    """If the hook can't be registered the request must still commit — degraded
    to the old late-commit ordering, never to a lost write."""
    events: list[str] = []
    resp = await _post(_build_app(events, register=False))

    assert resp.status_code == 200
    assert events.count("commit") == 1, events
    # Degraded ordering is the documented fallback, so assert it explicitly.
    assert events.index(RESPONSE_START) < events.index("commit"), events


def test_returns_false_without_a_usable_stack():
    class _Req:
        scope: dict = {}

    assert commit_before_response(FakeSession([]), _Req()) is False  # type: ignore[arg-type]
    assert commit_before_response(FakeSession([]), None) is False


# ---------------------------------------------------------------------------
# Drift guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fastapi_still_exposes_the_pre_send_exit_stack():
    """`fastapi_function_astack` is a FastAPI internal. If an upgrade renames or
    removes it, `commit_before_response` degrades silently to the old ordering —
    correct, but racy again. Fail loudly here instead.

    If this breaks on a FastAPI bump: find the exit stack FastAPI unwinds before
    `await response(scope, receive, send)` in `routing.get_request_handler` and
    update `_FUNCTION_ASTACK_KEY`. Do not just delete this test.
    """
    seen: dict[str, object] = {}
    app = FastAPI()

    @app.get("/probe")
    async def probe(request: Request) -> dict:
        seen["stack"] = request.scope.get(_FUNCTION_ASTACK_KEY)
        return {}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        await c.get("/probe")

    assert isinstance(seen.get("stack"), AsyncExitStack), (
        f"FastAPI no longer exposes scope[{_FUNCTION_ASTACK_KEY!r}] as an "
        f"AsyncExitStack; got {type(seen.get('stack'))!r}"
    )


# ---------------------------------------------------------------------------
# Wiring — the real providers, against a real tenant DB
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_endpoint_commits_before_responding(realdb):
    """End-to-end: a real mutating route on a real tenant DB commits before the
    response ships. Pins the *wiring*, not just the helper."""
    from sqlalchemy.ext.asyncio import AsyncSession

    events: list[str] = []
    original = AsyncSession.commit

    async def traced(self):
        events.append("commit")
        return await original(self)

    AsyncSession.commit = traced  # type: ignore[method-assign]
    try:
        async with realdb.client(key="a") as client:
            recorder = SendRecorder(client._transport.app, events)  # type: ignore[attr-defined]
            client._transport.app = recorder  # type: ignore[attr-defined]
            resp = await client.post(
                "/api/invoices",
                json={
                    "invoice_number": "COMMIT-ORDER-1",
                    "vendor": "Ordering Probe Vendor",
                    "amount": "10.00",
                    "currency": "USD",
                },
            )
    finally:
        AsyncSession.commit = original  # type: ignore[method-assign]

    assert resp.status_code in (200, 201), resp.text
    assert RESPONSE_START in events, events
    start = events.index(RESPONSE_START)
    assert "commit" in events[:start], f"no commit before the response was sent, got: {events}"
    assert not [e for e in events[start:] if e == "commit"], (
        f"commit(s) landed AFTER the response was sent, got: {events}"
    )
