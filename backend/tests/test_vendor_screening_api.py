"""Sanctions / vendor-risk screening — HTTP surface + the payment-block gate.

Covers the roadmap "Sanctions & Vendor Risk Screening" slice owned by this
worker:

  * screen-on-create: a blocklisted vendor name lands `screening_status=match`
    and `payments_blocked=True`, with an `initial` `sanctions_checks` row;
  * a clean vendor screens `clear` and stays payable;
  * the per-vendor screening history + the org-wide review queue surface the
    trail / the flagged vendor;
  * a manual `POST /{id}/screen` on a clean vendor returns clear;
  * the payment-compliance gate refuses a blocked vendor and clears once
    unblocked.

All DB-backed via the `realdb` harness (the same pattern as
`test_vendor_change_request_approval.py`). The default `mock` sanctions
adapter needs no key: the fixture names "Sanctioned Test Entity" /
"OFAC SDN Fixture" / "Blocked Party LLC" always hit.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.sanctions_check import SanctionsCheck
from app.models.vendor import Vendor
from app.services.compliance import check_payment_compliance

TENANT = "a"


@pytest.fixture
def mk(realdb):
    return realdb.sessionmaker(TENANT)


# ---------------------------------------------------------------------------
# Screen on create.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_blocklisted_vendor_is_matched_and_blocked(realdb, mk):
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            "/api/vendors",
            json={"name": "Sanctioned Test Entity", "code": "STE-1"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    # Response reflects the screen (built AFTER screening).
    assert body["screening_status"] == "match"
    assert body["payments_blocked"] is True

    vendor_id = uuid.UUID(body["id"])
    async with mk() as s:
        rows = (
            (await s.execute(select(SanctionsCheck).where(SanctionsCheck.vendor_id == vendor_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].check_type == "initial"
    assert rows[0].result == "match"


@pytest.mark.asyncio
async def test_create_normal_vendor_is_clear_and_payable(realdb, mk):
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            "/api/vendors",
            json={"name": "Totally Normal Supplies Co", "code": "TNS-1"},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["screening_status"] == "clear"
    assert body["payments_blocked"] is False

    vendor_id = uuid.UUID(body["id"])
    async with mk() as s:
        rows = (
            (await s.execute(select(SanctionsCheck).where(SanctionsCheck.vendor_id == vendor_id)))
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].result == "clear"
    assert rows[0].check_type == "initial"


# ---------------------------------------------------------------------------
# Screening history + review queue.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_screening_history_returns_rows(realdb):
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors", json={"name": "OFAC SDN Fixture", "code": "OSF-1"}
        )
        vendor_id = created.json()["id"]
        # A second (manual) screen so the trail has > 1 row.
        await client.post(f"/api/vendors/{vendor_id}/screen")

        resp = await client.get(f"/api/vendors/{vendor_id}/screening-history")
    assert resp.status_code == 200, resp.text
    history = resp.json()
    assert len(history) == 2
    # Newest first; both are matches.
    assert {h["check_type"] for h in history} == {"initial", "manual"}
    assert all(h["result"] == "match" for h in history)
    assert all(h["vendor_id"] == vendor_id for h in history)


@pytest.mark.asyncio
async def test_review_queue_includes_matched_vendor(realdb):
    async with realdb.client(key=TENANT, role="ap_manager") as client:
        created = await client.post(
            "/api/vendors", json={"name": "Blocked Party LLC", "code": "BP-1"}
        )
        vendor_id = created.json()["id"]

        resp = await client.get("/api/vendors/screening/review-queue")
    assert resp.status_code == 200, resp.text
    queue = resp.json()
    mine = [it for it in queue if it["vendor_id"] == vendor_id]
    assert mine, "the matched vendor should appear in the review queue"
    assert mine[0]["screening_status"] == "match"
    assert mine[0]["payments_blocked"] is True
    assert mine[0]["latest_matched_list"] == "MOCK_TEST_SDN"
    assert mine[0]["latest_provider"] == "mock"


@pytest.mark.asyncio
async def test_review_queue_literal_route_not_shadowed(realdb):
    """`GET /vendors/screening/review-queue` must hit the queue handler, not
    the `/{vendor_id}` route (which expects a UUID)."""
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get("/api/vendors/screening/review-queue")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# Manual screen.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manual_screen_on_normal_vendor_returns_clear(realdb):
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors", json={"name": "Quiet Vendor Inc", "code": "QV-1"}
        )
        vendor_id = created.json()["id"]
        resp = await client.post(f"/api/vendors/{vendor_id}/screen")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["screening_status"] == "clear"
    assert body["payments_blocked"] is False


@pytest.mark.asyncio
async def test_manual_screen_missing_vendor_404(realdb):
    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(f"/api/vendors/{uuid.uuid4()}/screen")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Block / unblock + the payment-compliance gate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_block_gate_refuses_then_unblock_clears(realdb, mk):
    org_id = realdb.info(TENANT).org_id
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors", json={"name": "Manual Block Target", "code": "MBT-1"}
        )
        vendor_id = created.json()["id"]

        # Manually block.
        blocked = await client.post(
            f"/api/vendors/{vendor_id}/block", json={"reason": "BEC investigation"}
        )
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["payments_blocked"] is True

    # The compliance gate must refuse a blocked vendor before any adapter call.
    async with mk() as s:
        vendor = (
            await s.execute(select(Vendor).where(Vendor.id == uuid.UUID(vendor_id)))
        ).scalar_one()
        decision = await check_payment_compliance(
            s,
            vendor=vendor,
            payment_amount=Decimal("100.00"),
            payment_method="ach",
            org_settings=None,
            organization_id=org_id,
        )
    assert decision.verdict == "refuse"
    assert any("blocked from payment" in r for r in decision.reasons)
    assert any("BEC investigation" in r for r in decision.reasons)

    # Unblock and confirm the gate now allows.
    async with realdb.client(key=TENANT, role="admin") as client:
        unblocked = await client.post(f"/api/vendors/{vendor_id}/unblock")
        assert unblocked.status_code == 200, unblocked.text
        assert unblocked.json()["payments_blocked"] is False

    async with mk() as s:
        vendor = (
            await s.execute(select(Vendor).where(Vendor.id == uuid.UUID(vendor_id)))
        ).scalar_one()
        decision = await check_payment_compliance(
            s,
            vendor=vendor,
            payment_amount=Decimal("100.00"),
            payment_method="ach",
            org_settings=None,
            organization_id=org_id,
        )
    # A clean domestic ACH to a clear vendor allows.
    assert decision.verdict == "allow"


@pytest.mark.asyncio
async def test_blocked_object_refused_unit(realdb):
    """The key invariant in isolation: a Vendor with payments_blocked=True is
    refused before any sanctions adapter is even consulted."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        vendor = Vendor(
            id=uuid.uuid4(),
            name="Already Blocked Co",
            organization_id=org_id,
            status="active",
            source="manual",
            payments_blocked=True,
            payments_blocked_reason="sanctions match (MOCK_TEST_SDN) via mock",
        )
        decision = await check_payment_compliance(
            s,
            vendor=vendor,
            payment_amount=Decimal("5000.00"),
            payment_method="international_wire",
            org_settings=None,
            organization_id=org_id,
        )
    assert decision.verdict == "refuse"
    assert decision.screening_result is None  # short-circuited before screening
    assert any("MOCK_TEST_SDN" in r for r in decision.reasons)


@pytest.mark.asyncio
async def test_clerk_cannot_block(realdb):
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors", json={"name": "Clerk Block Probe", "code": "CBP-1"}
        )
        vendor_id = created.json()["id"]
    async with realdb.client(key=TENANT, role="ap_clerk") as client:
        resp = await client.post(f"/api/vendors/{vendor_id}/block")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Re-screen on identity-relevant update.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_to_blocklisted_name_rescreens_and_blocks(realdb, mk):
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors", json={"name": "Will Become Bad", "code": "WBB-1"}
        )
        vendor_id = created.json()["id"]
        # Rename onto the blocklist → a fresh screen flips it to match.
        patched = await client.patch(
            f"/api/vendors/{vendor_id}", json={"name": "Blocked Party LLC"}
        )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["screening_status"] == "match"
    assert body["payments_blocked"] is True

    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(SanctionsCheck).where(SanctionsCheck.vendor_id == uuid.UUID(vendor_id))
                )
            )
            .scalars()
            .all()
        )
    # initial (clear, on create) + initial (match, on rename).
    assert len(rows) == 2
