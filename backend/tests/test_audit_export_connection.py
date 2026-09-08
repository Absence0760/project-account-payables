"""The SOX export releases its tenant connection before it transmits.

Streaming (`decisions §105`) bounded the export's memory and, in the same
stroke, extended the connection hold from "while the rows are read" to "until
the last byte reaches the client". The per-tenant pool is
`pool_size=5, max_overflow=10`, so a handful of concurrent annual exports over
slow links could exhaust it while the database itself sat completely idle —
paying a pooled resource for the client's bandwidth.

The fix keeps both properties instead of trading one for the other: the rendered
body is drained into a `SpooledTemporaryFile` (bounded RAM, then disk), the
tenant session is closed, and only then is the response returned. This file
pins the part that is easy to lose in a later refactor —

* **the connection really is back**, asserted at the moment the first body chunk
  leaves, on the engine's own pool checkout/checkin events rather than on
  anything this test taught to say so;
* **the bytes did not change**, compared against the module's independent
  materialising renderer;
* **the temp file is closed on both exits** — the last block read, and the
  client hanging up mid-download.

`test_audit_export_streaming.py` still owns the other half of the contract: that
the response is chunked and that it never truncates.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import tempfile
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, Request
from sqlalchemy import event, select

from app.api import audit as audit_api
from app.models.organization import Organization
from app.models.workflow import AuditLog
from app.tenant import get_tenant

TENANT = "a"

# Enough rendered bytes to cross several `_SPOOL_READ_BYTES` blocks, so the
# reassembly path is genuinely exercised rather than trivially satisfied.
SEEDED_ROWS = 1200

# Non-ASCII on purpose: the spool slices BYTES, so a multi-byte character
# straddling a block boundary is the obvious way to corrupt an export that
# looked fine on ASCII fixtures.
UNICODE_MARK = "Ünïcøde — 供給者 ✓"


async def _seed(mk, org_id, n: int, *, entity_type: str) -> datetime:
    base = datetime.now(UTC) - timedelta(days=2)
    async with mk() as s:
        for i in range(n):
            s.add(
                AuditLog(
                    correlation_id=uuid.uuid4(),
                    organization_id=org_id,
                    actor_id=None,
                    action="spool.probe",
                    entity_type=entity_type,
                    entity_id=uuid.uuid4(),
                    details={"seq": i, "note": UNICODE_MARK},
                    created_at=base + timedelta(seconds=i),
                )
            )
        await s.commit()
    return base


def _range_qs(start: datetime, *, fmt: str | None = None) -> str:
    qs = (
        f"start={start.date().isoformat()}"
        f"&end={(start + timedelta(days=7)).date().isoformat()}"
        "&entity_type=spool_probe"
    )
    return qs if fmt is None else f"{qs}&format={fmt}"


class _TenantSessionProbe:
    """Watches the ONE tenant session an export request is given.

    Wraps the `realdb` client's own `get_tenant_db` override rather than
    replacing it, so the request still runs the real provider (including the
    `get_tenant` org-claim cross-check) and the real commit-before-response
    semantics. The pool listeners go on the engine that session is bound to,
    before it has issued its first query — so `live_connections` counts real
    checkouts against a real Postgres, not anything this test simulates.
    """

    def __init__(self) -> None:
        self.sessions: list = []
        self._checkouts = 0
        self._checkins = 0

    @property
    def live_connections(self) -> int:
        return self._checkouts - self._checkins

    def install(self) -> None:
        from app.main import app
        from app.tenant import get_tenant_db

        inner = app.dependency_overrides[get_tenant_db]
        probe = self

        # The signature is restated rather than copied off `inner`: FastAPI
        # resolves a dependency's annotations against the function's OWN
        # `__globals__`, and both this module and conftest use
        # `from __future__ import annotations`, so a borrowed signature arrives
        # as unresolvable strings and `request` is demanded as a query
        # parameter. It mirrors the production `get_tenant_db`, keeping the
        # `get_tenant` org-claim cross-check in the chain — the thing conftest's
        # own override exists to preserve.
        async def _recording(request: Request, tenant: Organization = Depends(get_tenant)):
            agen = inner(request, tenant)
            try:
                async for session in agen:
                    if not probe.sessions:
                        probe._watch(session)
                    probe.sessions.append(session)
                    yield session
            finally:
                # Deterministic teardown of the wrapped provider: leaving it to
                # async-generator finalization would close the session at some
                # later GC pass, which is exactly the ambiguity under test.
                await agen.aclose()

        app.dependency_overrides[get_tenant_db] = _recording

    def _watch(self, session) -> None:
        bind = session.bind
        engine = getattr(bind, "sync_engine", bind)
        event.listen(engine, "checkout", self._on_checkout)
        event.listen(engine, "checkin", self._on_checkin)

    def _on_checkout(self, *_a, **_k) -> None:
        self._checkouts += 1

    def _on_checkin(self, *_a, **_k) -> None:
        self._checkins += 1


async def _drive_asgi(client, path: str):
    """Call the ASGI app directly, sampling state at the first body chunk.

    `httpx.ASGITransport` hands back one concatenated body, so the only way to
    observe what is true *while* the response is being transmitted is to be the
    `send` callable. Returns (start message, body chunks, samples).
    """
    from app.main import app

    url, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": url,
        "raw_path": url.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in client.headers.items()],
        "client": ("127.0.0.1", 5000),
        "server": ("test", 80),
    }

    delivered = False
    never = asyncio.Event()

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await never.wait()
        raise AssertionError("unreachable")  # pragma: no cover

    start: dict = {}
    chunks: list[bytes] = []
    samples: list = []

    async def send(message):
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body":
            if message.get("body"):
                samples.append(_SAMPLER[0]() if _SAMPLER else None)
                chunks.append(message["body"])

    await app(scope, receive, send)
    return start, chunks, samples


# Set by the connection test; `_drive_asgi` calls it once per body chunk.
_SAMPLER: list = []


# ---------------------------------------------------------------------------
# The connection is back in the pool before a byte is transmitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["json", "csv"])
async def test_export_releases_the_tenant_connection_before_the_body_is_sent(realdb, fmt):
    """No checked-out tenant connection while the export transmits.

    Before the spool this failed: the `yield_per` cursor stayed open across the
    whole body, so the session was still in its read transaction — holding one
    of the tenant's five pooled connections — for as long as the download took.
    """
    info = realdb.info(TENANT)
    start = await _seed(
        realdb.sessionmaker(TENANT), info.org_id, SEEDED_ROWS, entity_type="spool_probe"
    )

    probe = _TenantSessionProbe()
    async with realdb.client(key=TENANT, role="admin") as client:
        probe.install()
        _SAMPLER[:] = [lambda: (probe.live_connections, probe.sessions[0].in_transaction())]
        try:
            response_start, chunks, samples = await _drive_asgi(
                client, f"/api/audit/export?{_range_qs(start, fmt=fmt)}"
            )
        finally:
            _SAMPLER.clear()

    assert response_start["status"] == 200
    assert len(chunks) > 1, "the export was not chunked — the sample is meaningless"
    assert probe.sessions, "the export never took a tenant session"

    live_at_first_byte, in_transaction_at_first_byte = samples[0]
    assert live_at_first_byte == 0, (
        f"{live_at_first_byte} tenant connection(s) were still checked out when the "
        f"first {fmt} body chunk was sent — the export is holding a pooled "
        "connection for the client's bandwidth"
    )
    assert not in_transaction_at_first_byte, (
        "the tenant session was still inside its read transaction while the body "
        "was being transmitted"
    )
    # And it stays released for every later chunk, not just the first.
    assert {live for live, _ in samples} == {0}


# ---------------------------------------------------------------------------
# The bytes did not change
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_export_is_byte_identical_to_the_materialising_renderer(realdb):
    """Spooling must be invisible in the output.

    The comparison is against `_entries_to_csv` — the module's OTHER rendering
    of the same rows, which shares `_CSV_HEADER` / `_csv_row` but none of the
    chunking or the spool. A block boundary that dropped, duplicated or split a
    character shows up here as a byte difference, and the fixture is
    deliberately non-ASCII because the spool slices bytes, not characters.
    """
    info = realdb.info(TENANT)
    start = await _seed(
        realdb.sessionmaker(TENANT), info.org_id, SEEDED_ROWS, entity_type="spool_probe"
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get(f"/api/audit/export?{_range_qs(start, fmt='csv')}")

    assert resp.status_code == 200, resp.text
    assert len(resp.content) > audit_api._SPOOL_READ_BYTES, (
        "fixture too small to cross a spool read block"
    )

    async with realdb.sessionmaker(TENANT)() as s:
        rows = (
            await s.execute(
                select(
                    AuditLog.id,
                    AuditLog.correlation_id,
                    AuditLog.actor_id,
                    AuditLog.action,
                    AuditLog.entity_type,
                    AuditLog.entity_id,
                    AuditLog.details,
                    AuditLog.created_at,
                )
                .where(AuditLog.entity_type == "spool_probe")
                .order_by(AuditLog.created_at)
            )
        ).all()
    expected = audit_api._entries_to_csv(
        [audit_api.AuditExportEntry.from_db(row, {}, {}) for row in rows]
    )

    assert resp.text == expected
    # And the unicode survived end to end, not merely "some bytes came back".
    parsed = list(csv.reader(io.StringIO(resp.text)))
    assert len(parsed) == SEEDED_ROWS + 1
    assert all(UNICODE_MARK in r[-1] for r in parsed[1:])


@pytest.mark.asyncio
async def test_json_export_survives_the_spool_intact(realdb):
    info = realdb.info(TENANT)
    start = await _seed(
        realdb.sessionmaker(TENANT), info.org_id, SEEDED_ROWS, entity_type="spool_probe"
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get(f"/api/audit/export?{_range_qs(start)}")

    assert resp.status_code == 200, resp.text
    body = json.loads(resp.text)
    assert [e["details"]["seq"] for e in body] == list(range(SEEDED_ROWS))
    assert all(e["details"]["note"] == UNICODE_MARK for e in body)


# ---------------------------------------------------------------------------
# The temp file is closed on both exits
# ---------------------------------------------------------------------------


async def _chunks(*values: str):
    for value in values:
        yield value


@pytest.mark.asyncio
async def test_spool_is_closed_once_the_last_block_is_read():
    """`SpooledTemporaryFile.close()` is what unlinks the on-disk file, so a
    drain that finishes without closing leaks one temp file per export."""
    spool = await audit_api._spool_body(_chunks("alpha", "beta"))
    assert not spool.closed

    collected = b"".join([block async for block in audit_api._drain_spool(spool)])

    assert collected == b"alphabeta"
    assert spool.closed


@pytest.mark.asyncio
async def test_spool_is_closed_when_the_client_hangs_up_mid_download():
    """Starlette closes the body generator when the client disconnects, which
    raises GeneratorExit inside `_drain_spool`. The abandoned download is the
    case that leaks quietly, because nothing surfaces an error."""
    # Comfortably past one read block, so there is genuinely more to send.
    payload = "x" * (audit_api._SPOOL_READ_BYTES * 3)
    spool = await audit_api._spool_body(_chunks(payload))

    body = audit_api._drain_spool(spool)
    first = await anext(body)
    assert len(first) == audit_api._SPOOL_READ_BYTES
    assert not spool.closed

    await body.aclose()

    assert spool.closed, "the client hung up and the temp file was left open"


@pytest.mark.asyncio
async def test_a_render_failure_closes_the_spool_rather_than_leaking_it(monkeypatch):
    """A database error part-way through the range now surfaces BEFORE any byte
    is sent — which is better for the caller, but only if the partially written
    spool goes away with it. Nothing downstream ever sees this file, so the
    render frame is the only place that can close it.

    The factory records the REAL `SpooledTemporaryFile` it constructs (it does
    not stand in for one), so `closed` below is the file object's own state.
    """
    made = []
    real = tempfile.SpooledTemporaryFile

    def _recording(*args, **kwargs):
        spool = real(*args, **kwargs)
        made.append(spool)
        return spool

    monkeypatch.setattr(audit_api.tempfile, "SpooledTemporaryFile", _recording)
    boom = RuntimeError("cursor died mid-range")

    async def _failing():
        yield "partial"
        raise boom

    with pytest.raises(RuntimeError) as exc:
        await audit_api._spool_body(_failing())

    assert exc.value is boom
    assert made and made[0].closed, "the half-written export spool was left open"


@pytest.mark.asyncio
async def test_spool_holds_more_than_it_keeps_in_memory():
    """Past `_SPOOL_MAX_MEMORY_BYTES` the rest rolls to disk, so a year-long
    range is bounded by that number rather than by its own size — the property
    that lets the connection be released without giving the memory back."""
    oversized = "y" * (audit_api._SPOOL_MAX_MEMORY_BYTES + 4096)
    spool = await audit_api._spool_body(_chunks(oversized))
    try:
        collected = b"".join([block async for block in audit_api._drain_spool(spool)])
    finally:
        spool.close()

    assert collected == oversized.encode("utf-8")
