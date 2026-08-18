"""Quality Inspection API — CRUD + entity scoping over the real ASGI app.

Drives ``/api/inspections`` through the realdb client: POST create (201),
GET list + detail, a bad ``result`` → 400, and ``X-Entity-ID`` list scoping
(one inspection per entity; scoped view sees its own, consolidated sees both).
"""

from __future__ import annotations

import uuid

import pytest


async def _create_entity(c, *, name: str, slug: str) -> str:
    resp = await c.post("/api/entities", json={"name": name, "slug": slug})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


@pytest.mark.parametrize("header_role", ["admin"])
async def test_create_and_get_inspection(realdb, header_role):
    async with realdb.client(key="a", role=header_role) as c:
        resp = await c.post(
            "/api/inspections",
            json={
                "inspection_number": "QI-100",
                "result": "partial",
                "inspector": "Sam Lee",
                "accepted_quantity": "8.0000",
                "rejected_quantity": "2.0000",
                "deviation_notes": "Minor scuffs",
            },
        )
        assert resp.status_code == 201, resp.text
        created = resp.json()
        assert created["inspection_number"] == "QI-100"
        assert created["result"] == "partial"
        assert created["accepted_quantity"] == 8.0
        iid = created["id"]

        # Detail.
        detail = await c.get(f"/api/inspections/{iid}")
        assert detail.status_code == 200
        assert detail.json()["inspector"] == "Sam Lee"

        # List.
        lst = await c.get("/api/inspections")
        assert lst.status_code == 200
        assert iid in {row["id"] for row in lst.json()}


async def test_get_unknown_inspection_is_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/inspections/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_create_bad_result_is_400(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/inspections",
            json={"inspection_number": "QI-BAD", "result": "rejected"},
        )
    assert resp.status_code == 400, resp.text


async def test_create_requires_role(realdb):
    # ap_clerk is neither admin nor ap_manager → 403.
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/inspections",
            json={"inspection_number": "QI-RBAC", "result": "pass"},
        )
    assert resp.status_code == 403, resp.text


async def test_list_scopes_by_entity(realdb):
    async with realdb.client(key="a", role="admin") as c:
        us = await _create_entity(c, name="US Inc", slug="us")

        # One inspection under US, one under the default entity (no header).
        r_us = await c.post(
            "/api/inspections",
            json={"inspection_number": "QI-US", "result": "pass"},
            headers={"X-Entity-ID": us},
        )
        assert r_us.status_code == 201, r_us.text
        r_def = await c.post(
            "/api/inspections",
            json={"inspection_number": "QI-DEF", "result": "fail"},
        )
        assert r_def.status_code == 201, r_def.text

        # Scoped to US → only the US inspection.
        scoped = await c.get("/api/inspections", headers={"X-Entity-ID": us})
        assert {r["inspection_number"] for r in scoped.json()} == {"QI-US"}

        # Consolidated (no header) → both.
        allv = await c.get("/api/inspections")
        assert {r["inspection_number"] for r in allv.json()} == {"QI-US", "QI-DEF"}


async def test_sync_refuses_when_no_qms_is_configured(realdb):
    """The manual sync must apply the same opt-in rule the sweep does.

    Without a `settings.qms` block, `get_qms_adapter(None)` resolves to the
    `mock` adapter — so one call used to persist its three fabricated fixtures
    (`QMS-INSP-001 pass / PO-1001` …) against the tenant's REAL purchase
    orders, clearing or failing the 4-way quality gate on real invoices with
    rows indistinguishable from genuine ones.
    """
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/inspections/sync")
        assert resp.status_code == 409, resp.text
        assert "qms" in resp.json()["detail"].lower()

        # And nothing from the mock fixture set landed.
        listing = await c.get("/api/inspections")
        assert not [
            r for r in listing.json() if str(r["inspection_number"]).startswith("QMS-INSP-")
        ]
