"""Real-DB coverage for GET /api/exceptions/ids.

Mirrors `test_invoice_ids.py`: the "select all N matching" affordance on the
exceptions queue resolves its selection through this endpoint rather than
the currently-LOADED page of `GET /api/exceptions`, so a bulk resolve can
cover the whole filtered set, not just the loaded page.
"""

from __future__ import annotations

import pytest

from app.models.exception import Exception as APException


async def _add_exceptions(mk, org_id, status: str, n: int, *, exc_type="duplicate") -> list[str]:
    objs: list[APException] = []
    async with mk() as s:
        for i in range(n):
            exc = APException(
                organization_id=org_id,
                exception_type=exc_type,
                severity="warning",
                description=f"seed-{status}-{i}",
                status=status,
            )
            s.add(exc)
            objs.append(exc)
        await s.flush()
        ids = [str(e.id) for e in objs]
        await s.commit()
    return ids


@pytest.mark.asyncio
async def test_ids_exceeds_a_single_list_page(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    created = await _add_exceptions(mk, org_id, "open", 45)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/exceptions/ids", params={"status": "open"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 45
    assert body["truncated"] is False
    assert set(body["ids"]) == set(created)


@pytest.mark.asyncio
async def test_ids_honours_type_filter(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    matching = await _add_exceptions(mk, org_id, "open", 3, exc_type="fraud_flag")
    await _add_exceptions(mk, org_id, "open", 3, exc_type="duplicate")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/exceptions/ids", params={"type": "fraud_flag"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert set(body["ids"]) == set(matching)


@pytest.mark.asyncio
async def test_ids_open_and_escalated_only(realdb):
    """The frontend's "select all matching" is bulk-resolve-only, so it
    always requests `status=open,escalated` — a resolved/dismissed row must
    never come back even without an explicit status filter here."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    open_ids = await _add_exceptions(mk, org_id, "open", 2)
    escalated_ids = await _add_exceptions(mk, org_id, "escalated", 2)
    await _add_exceptions(mk, org_id, "resolved", 2)
    await _add_exceptions(mk, org_id, "dismissed", 2)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/exceptions/ids", params={"status": "open,escalated"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 4
    assert set(body["ids"]) == set(open_ids) | set(escalated_ids)


@pytest.mark.asyncio
async def test_ids_truncates_past_the_cap(realdb, monkeypatch):
    monkeypatch.setattr("app.api.exceptions.MAX_SELECT_ALL_IDS", 5, raising=True)
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_exceptions(mk, org_id, "open", 8)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get("/api/exceptions/ids", params={"status": "open"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 8
    assert len(body["ids"]) == 5
    assert body["truncated"] is True


@pytest.mark.asyncio
async def test_ids_requires_ap_manager_role(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/exceptions/ids")
    assert resp.status_code == 403
