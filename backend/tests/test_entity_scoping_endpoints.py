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


# ---------------------------------------------------------------------------
# Vendors
# ---------------------------------------------------------------------------


async def test_vendor_list_scopes_by_entity(realdb):
    async with realdb.client(key="a", role="admin") as c:
        us = await _create_entity(c, name="US Inc", slug="us")

        r_us = await c.post("/api/vendors", json={"name": "US Vendor"}, headers={"X-Entity-ID": us})
        assert r_us.status_code == 201, r_us.text
        r_def = await c.post("/api/vendors", json={"name": "Default Vendor"})
        assert r_def.status_code == 201, r_def.text

        scoped = await c.get("/api/vendors", headers={"X-Entity-ID": us})
        names = {v["name"] for v in scoped.json()["items"]}
        assert names == {"US Vendor"}

        allv = await c.get("/api/vendors")
        assert {v["name"] for v in allv.json()["items"]} == {"US Vendor", "Default Vendor"}


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


async def test_payment_list_and_summary_scope_by_entity(realdb):
    async with realdb.client(key="a", role="admin") as c:
        us = await _create_entity(c, name="US Inc", slug="us")

        # An invoice + a manual payment under each entity. The payment inherits
        # its invoice's entity.
        async def _invoice_and_payment(headers, num, amt):
            inv = await c.post(
                "/api/invoices",
                json={"invoice_number": num, "vendor": "Acme", "amount": amt},
                headers=headers,
            )
            assert inv.status_code == 201, inv.text
            pay = await c.post(
                "/api/payments",
                json={"invoice_id": inv.json()["id"], "amount": amt, "method": "ach"},
            )
            assert pay.status_code == 201, pay.text
            return pay.json()

        await _invoice_and_payment({"X-Entity-ID": us}, "US-P", "100.00")
        await _invoice_and_payment({}, "DEF-P", "200.00")

        scoped = await c.get("/api/payments", headers={"X-Entity-ID": us})
        assert scoped.json()["total"] == 1
        allv = await c.get("/api/payments")
        assert allv.json()["total"] == 2

        # Summary KPI count mirrors the scoping.
        s_us = await c.get("/api/payments/summary", headers={"X-Entity-ID": us})
        assert s_us.json()["payment_count"] == 1
        s_all = await c.get("/api/payments/summary")
        assert s_all.json()["payment_count"] == 2


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


async def test_dashboard_scopes_by_entity(realdb):
    async with realdb.client(key="a", role="admin") as c:
        us = await _create_entity(c, name="US Inc", slug="us")

        await c.post(
            "/api/invoices",
            json={"invoice_number": "D-US", "vendor": "Acme", "amount": "100.00"},
            headers={"X-Entity-ID": us},
        )
        await c.post(
            "/api/invoices",
            json={"invoice_number": "D-DEF", "vendor": "Beta", "amount": "200.00"},
        )

        scoped = (await c.get("/api/dashboard", headers={"X-Entity-ID": us})).json()
        assert scoped["total_invoices"] == 1
        assert scoped["total_amount"] == 100.0

        allv = (await c.get("/api/dashboard")).json()
        assert allv["total_invoices"] == 2
        assert allv["total_amount"] == 300.0


# ---------------------------------------------------------------------------
# Credit memos
# ---------------------------------------------------------------------------


async def test_credit_memo_list_scopes_by_entity(realdb):
    async with realdb.client(key="a", role="admin") as c:
        us = await _create_entity(c, name="US Inc", slug="us")

        # Vendor per entity; each credit memo inherits its vendor's entity.
        v_us = (
            await c.post("/api/vendors", json={"name": "US V"}, headers={"X-Entity-ID": us})
        ).json()
        v_def = (await c.post("/api/vendors", json={"name": "Def V"})).json()
        for vid, num in ((v_us["id"], "CM-US"), (v_def["id"], "CM-DEF")):
            r = await c.post(
                "/api/credit-memos",
                json={"memo_number": num, "vendor_id": vid, "amount": "10.00"},
            )
            assert r.status_code == 201, r.text

        scoped = await c.get("/api/credit-memos", headers={"X-Entity-ID": us})
        assert {m["memo_number"] for m in scoped.json()["items"]} == {"CM-US"}
        allv = await c.get("/api/credit-memos")
        assert {m["memo_number"] for m in allv.json()["items"]} == {"CM-US", "CM-DEF"}


# ---------------------------------------------------------------------------
# GL accounts — shared chart (NULL = shared) ∪ entity's own
# ---------------------------------------------------------------------------


async def test_gl_account_shared_chart_scoping(realdb):
    async with realdb.client(key="a", role="admin") as c:
        us = await _create_entity(c, name="US Inc", slug="us")
        default_id = await _default_entity_id(c)

        # Shared account created in the consolidated view (no header) -> NULL.
        r_shared = await c.post(
            "/api/gl-accounts", json={"code": "1000", "name": "Cash", "account_type": "asset"}
        )
        assert r_shared.status_code == 201, r_shared.text
        # Entity-specific account created under US.
        r_us = await c.post(
            "/api/gl-accounts",
            json={"code": "6000", "name": "US Marketing", "account_type": "expense"},
            headers={"X-Entity-ID": us},
        )
        assert r_us.status_code == 201, r_us.text

        # Scoped to US -> shared (Cash) ∪ US's own (US Marketing).
        us_codes = {
            a["code"] for a in (await c.get("/api/gl-accounts", headers={"X-Entity-ID": us})).json()
        }
        assert us_codes == {"1000", "6000"}

        # Scoped to the default entity -> shared only (US's account is hidden).
        def_codes = {
            a["code"]
            for a in (await c.get("/api/gl-accounts", headers={"X-Entity-ID": default_id})).json()
        }
        assert def_codes == {"1000"}

        # Consolidated -> everything.
        all_codes = {a["code"] for a in (await c.get("/api/gl-accounts")).json()}
        assert all_codes == {"1000", "6000"}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


async def test_exception_list_and_summary_scope_by_entity(realdb):
    import uuid

    from app.models.exception import Exception as APException

    async with realdb.client(key="a", role="admin") as c:
        us = await _create_entity(c, name="US Inc", slug="us")
        default_id = await _default_entity_id(c)

        # An invoice per entity to hang exceptions off (FK requires an invoice).
        inv_us = (
            await c.post(
                "/api/invoices",
                json={"invoice_number": "E-US", "vendor": "X", "amount": "1.00"},
                headers={"X-Entity-ID": us},
            )
        ).json()
        inv_def = (
            await c.post(
                "/api/invoices",
                json={"invoice_number": "E-DEF", "vendor": "Y", "amount": "1.00"},
            )
        ).json()

    org_id = realdb.info("a").org_id
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        s.add(
            APException(
                invoice_id=uuid.UUID(inv_us["id"]),
                exception_type="missing_data",
                status="open",
                organization_id=org_id,
                entity_id=uuid.UUID(us),
            )
        )
        s.add(
            APException(
                invoice_id=uuid.UUID(inv_def["id"]),
                exception_type="missing_data",
                status="open",
                organization_id=org_id,
                entity_id=uuid.UUID(default_id),
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="admin") as c:
        scoped = await c.get("/api/exceptions", headers={"X-Entity-ID": us})
        assert scoped.json()["total"] == 1
        allv = await c.get("/api/exceptions")
        assert allv.json()["total"] == 2

        sum_us = await c.get("/api/exceptions/summary", headers={"X-Entity-ID": us})
        assert sum_us.json()["open"] == 1
        sum_all = await c.get("/api/exceptions/summary")
        assert sum_all.json()["open"] == 2
