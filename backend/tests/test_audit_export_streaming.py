"""`GET /api/audit/export` streams its evidence instead of materialising it.

The real use case for this endpoint is an annual SOX range — tens of thousands
of rows. It used to load every one of them as an ORM object and then build the
entire JSON or CSV body in memory on top of that, while its own sibling
`GET /audit/verify-signatures` had long since been converted to a server-side
cursor (`yield_per`).

Two things are pinned here, and they pull in opposite directions on purpose:

* **it streams** — the rows come off a cursor and the body leaves in chunks, so
  peak memory is a page rather than a period;
* **it does not truncate** — a `LIMIT` would have made the first property
  trivial to satisfy and the endpoint worthless, because an export that is
  silently short is evidence an auditor signs off on. Every seeded row must
  come back, in order, across every dialect.

Before the change the streaming cases failed: the response carried a
`content-length` (a single fully-buffered body), `AsyncSession.stream` was never
called, and the body arrived as exactly one chunk.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import audit as audit_api
from app.models.workflow import AuditLog

TENANT = "a"

# Comfortably more than one `_STREAM_CHUNK_ROWS` page, so a fix that streamed
# the query but still buffered the whole body — or one that quietly capped the
# result at a page — cannot pass.
SEEDED_ROWS = audit_api._STREAM_CHUNK_ROWS * 2 + 137


async def _seed_audit_rows(mk, org_id, n: int, *, entity_type: str) -> datetime:
    """`n` audit rows with strictly increasing `created_at`. Returns the start."""
    base = datetime.now(UTC) - timedelta(days=2)
    async with mk() as s:
        for i in range(n):
            s.add(
                AuditLog(
                    correlation_id=uuid.uuid4(),
                    organization_id=org_id,
                    actor_id=None,
                    action="stream.probe",
                    entity_type=entity_type,
                    entity_id=uuid.uuid4(),
                    details={"seq": i},
                    created_at=base + timedelta(seconds=i),
                )
            )
        await s.commit()
    return base


async def _drive_asgi(client, path: str) -> tuple[dict, list[bytes]]:
    """Call the ASGI app directly and return (start message, body messages).

    `httpx.ASGITransport` concatenates the whole body before handing it back —
    even `aiter_raw` yields a single chunk for a response the app emitted in
    five — so the number of pieces a `StreamingResponse` actually produces is
    invisible through the normal test client. Driving the app's own ASGI
    callable is the only way to observe it, and it observes the real thing: the
    `http.response.body` messages Starlette sends, in the order it sends them.

    The `realdb` client is still what installs the dependency overrides and
    carries the auth/tenant headers, so this exercises the same wired-up app.
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

    # The request body once, then block forever. `StreamingResponse` polls
    # `receive` for an `http.disconnect` alongside the body generator and
    # cancels that listener when the stream completes, so blocking is the
    # correct "client is still connected" answer — returning anything else
    # would either abort the export or trip Starlette's protocol check.
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
    bodies: list[bytes] = []

    async def send(message):
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body":
            if message.get("body"):
                bodies.append(message["body"])

    await app(scope, receive, send)
    return start, bodies


@pytest.fixture
def stream_calls(monkeypatch):
    """Count `AsyncSession.stream` calls — the server-side-cursor mechanism.

    This is the direct expression of "it streams rather than materialising":
    the materialising version reached `AsyncSession.execute` for the row set and
    never `.stream` at all.
    """
    calls: list[int] = []
    original = AsyncSession.stream

    async def counting(self, statement, *args, **kwargs):
        calls.append(1)
        return await original(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "stream", counting)
    return calls


# ---------------------------------------------------------------------------
# It streams
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("fmt", ["json", "csv"])
async def test_export_body_is_chunked_not_a_buffered_blob(realdb, fmt):
    """No `content-length`, and the body arrives in more than one piece.

    A fully-buffered `Response(content=...)` sets `content-length` and hands the
    transport exactly one chunk. Chunked framing is also what makes a failed
    export detectable: an aborted body has no terminating chunk, so a conforming
    client raises rather than accepting a short file as complete.
    """
    info = realdb.info(TENANT)
    start = await _seed_audit_rows(
        realdb.sessionmaker(TENANT), info.org_id, SEEDED_ROWS, entity_type="stream_probe"
    )
    qs = (
        f"start={start.date().isoformat()}"
        f"&end={(start + timedelta(days=7)).date().isoformat()}"
        f"&entity_type=stream_probe&format={fmt}"
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        response_start, chunks = await _drive_asgi(client, f"/api/audit/export?{qs}")

    assert response_start["status"] == 200
    header_names = {k.decode().lower() for k, _ in response_start["headers"]}
    assert "content-length" not in header_names, (
        "the export declared a Content-Length, so the whole body was buffered "
        "before the first byte was sent"
    )
    assert len(chunks) > 1, (
        f"the {fmt} export was sent as {len(chunks)} ASGI body message(s) for "
        f"{SEEDED_ROWS} rows — it is still being assembled in memory rather "
        "than emitted as it is read"
    )
    # Bounded pieces, not one blob followed by a crumb.
    assert max(len(c) for c in chunks) < sum(len(c) for c in chunks)


@pytest.mark.asyncio
async def test_export_reads_rows_through_a_server_side_cursor(realdb, stream_calls):
    info = realdb.info(TENANT)
    start = await _seed_audit_rows(
        realdb.sessionmaker(TENANT), info.org_id, 40, entity_type="stream_probe"
    )
    qs = (
        f"start={start.date().isoformat()}"
        f"&end={(start + timedelta(days=7)).date().isoformat()}"
        f"&entity_type=stream_probe"
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get(f"/api/audit/export?{qs}")

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 40
    assert stream_calls, (
        "the export never called AsyncSession.stream — the row set is still "
        "being materialised with .execute()"
    )


# ---------------------------------------------------------------------------
# It does not truncate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_json_export_returns_every_row_in_order(realdb):
    """The whole population, across many chunk boundaries, chronologically."""
    info = realdb.info(TENANT)
    start = await _seed_audit_rows(
        realdb.sessionmaker(TENANT), info.org_id, SEEDED_ROWS, entity_type="stream_probe"
    )
    qs = (
        f"start={start.date().isoformat()}"
        f"&end={(start + timedelta(days=7)).date().isoformat()}"
        f"&entity_type=stream_probe"
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get(f"/api/audit/export?{qs}")

    assert resp.status_code == 200, resp.text
    # Parsed, not merely non-empty: a stream that died mid-flight would leave
    # the array unclosed and this would raise.
    body = json.loads(resp.text)
    assert len(body) == SEEDED_ROWS, "the export came back short — evidence was lost"
    assert [e["details"]["seq"] for e in body] == list(range(SEEDED_ROWS))
    assert [e["created_at"] for e in body] == sorted(e["created_at"] for e in body)


@pytest.mark.asyncio
async def test_csv_export_returns_every_row_with_one_header(realdb):
    info = realdb.info(TENANT)
    start = await _seed_audit_rows(
        realdb.sessionmaker(TENANT), info.org_id, SEEDED_ROWS, entity_type="stream_probe"
    )
    qs = (
        f"start={start.date().isoformat()}"
        f"&end={(start + timedelta(days=7)).date().isoformat()}"
        f"&entity_type=stream_probe&format=csv"
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get(f"/api/audit/export?{qs}")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    rows = list(csv.reader(io.StringIO(resp.text)))
    # Exactly one header, emitted in the first chunk and never repeated per chunk.
    assert rows[0] == audit_api._CSV_HEADER
    assert len(rows) == SEEDED_ROWS + 1, "a chunk boundary swallowed or duplicated rows"
    assert all(r[1] == "stream.probe" for r in rows[1:])


@pytest.mark.asyncio
async def test_pdf_export_still_covers_the_whole_population(realdb):
    """The one dialect that must materialise still must not truncate."""
    info = realdb.info(TENANT)
    start = await _seed_audit_rows(
        realdb.sessionmaker(TENANT), info.org_id, 600, entity_type="stream_probe"
    )
    qs = (
        f"start={start.date().isoformat()}"
        f"&end={(start + timedelta(days=7)).date().isoformat()}"
        f"&entity_type=stream_probe&format=pdf"
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get(f"/api/audit/export?{qs}")

    assert resp.status_code == 200, resp.text
    assert resp.content.startswith(b"%PDF")
    # The `audit.exported` row this request wrote records the population it
    # covered; the PDF is rendered from that same set.
    async with realdb.sessionmaker(TENANT)() as s:
        row = (
            await s.execute(
                select(AuditLog.details)
                .where(AuditLog.action == "audit.exported")
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
    assert row["count"] == 600
    assert row["format"] == "pdf"


# ---------------------------------------------------------------------------
# The audit row the export writes describes the export honestly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exported_audit_row_counts_the_population_and_excludes_itself(realdb):
    """`count` is a real COUNT over the same predicate, and the export is a
    snapshot taken before the audit row lands.

    Streaming inverted the old ordering — the `audit.exported` row is now
    committed before the cursor opens — so without the snapshot bound the body
    would carry a row its own recorded `count` does not.
    """
    info = realdb.info(TENANT)
    start = await _seed_audit_rows(
        realdb.sessionmaker(TENANT), info.org_id, 25, entity_type="stream_probe"
    )
    # A whole-range export with no entity filter: the `audit.exported` row this
    # request writes WOULD fall inside it but for the snapshot bound.
    qs = f"start={start.date().isoformat()}&end={(start + timedelta(days=7)).date().isoformat()}"

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get(f"/api/audit/export?{qs}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert not [e for e in body if e["action"] == "audit.exported"], (
        "the export contained the record of itself — the snapshot bound is gone"
    )

    async with realdb.sessionmaker(TENANT)() as s:
        details = (
            await s.execute(
                select(AuditLog.details)
                .where(AuditLog.action == "audit.exported")
                .order_by(AuditLog.created_at.desc())
                .limit(1)
            )
        ).scalar_one()
    assert details["count"] == len(body), (
        "the audited row count and the delivered row count disagree"
    )
