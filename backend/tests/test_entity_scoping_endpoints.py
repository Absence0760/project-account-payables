"""Multi-entity Phase 2 — endpoint-level entity scoping (end to end).

Drives the real HTTP surface: a request with ``X-Entity-ID`` set sees only
that entity's rows; without the header (or with ``all``) it sees every
entity's rows (consolidated). Newly-created rows land under the entity named
by the header, or the tenant default when none is selected.

Grows as more areas are scoped; each area gets a small helper + a couple of
asserts here rather than a per-area file.
"""

from __future__ import annotations

import pytest


async def _create_entity(c, *, name: str, slug: str) -> str:
    resp = await c.post("/api/entities", json={"name": name, "slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _default_entity_id(c) -> str:
    rows = (await c.get("/api/entities")).json()
    return next(e["id"] for e in rows if e["is_default"])


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("header_role", ["admin"])
async def test_invoice_list_and_counts_scope_by_entity(realdb, header_role):
    async with realdb.client(key="a", role=header_role) as c:
        us = await _create_entity(c, name="US Inc", slug="us")
        default_id = await _default_entity_id(c)

        # One invoice under US, one under the default entity (no header).
        r_us = await c.post(
            "/api/invoices",
            json={"invoice_number": "US-1", "vendor": "Acme", "amount": "100.00"},
            headers={"X-Entity-ID": us},
        )
        assert r_us.status_code == 201, r_us.text
        r_def = await c.post(
            "/api/invoices",
            json={"invoice_number": "DEF-1", "vendor": "Beta", "amount": "200.00"},
        )
        assert r_def.status_code == 201, r_def.text

        # Scoped to US → only the US invoice.
        scoped = await c.get("/api/invoices", headers={"X-Entity-ID": us})
        nums = {i["invoice_number"] for i in scoped.json()["items"]}
        assert nums == {"US-1"}
        assert scoped.json()["total"] == 1

        # Scoped to the default entity → only the default invoice.
        scoped_def = await c.get("/api/invoices", headers={"X-Entity-ID": default_id})
        assert {i["invoice_number"] for i in scoped_def.json()["items"]} == {"DEF-1"}

        # No header → consolidated (both).
        allv = await c.get("/api/invoices")
        assert {i["invoice_number"] for i in allv.json()["items"]} == {"US-1", "DEF-1"}
        assert allv.json()["total"] == 2

        # Literal "all" → consolidated too.
        all_explicit = await c.get("/api/invoices", headers={"X-Entity-ID": "all"})
        assert all_explicit.json()["total"] == 2

        # Counts mirror the scoping.
        counts_us = await c.get("/api/invoices/counts", headers={"X-Entity-ID": us})
        assert counts_us.json()["total"] == 1
        counts_all = await c.get("/api/invoices/counts")
        assert counts_all.json()["total"] == 2


async def test_invoice_create_unknown_entity_header_is_400(realdb):
    import uuid

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/invoices",
            json={"invoice_number": "X-1", "vendor": "Acme", "amount": "1.00"},
            headers={"X-Entity-ID": str(uuid.uuid4())},
        )
    assert resp.status_code == 400


async def test_invoice_list_malformed_entity_header_is_400(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get("/api/invoices", headers={"X-Entity-ID": "not-a-uuid"})
    assert resp.status_code == 400
