"""Inter-company invoice routing (multi-entity) — HTTP + service.

Covers ``POST /api/invoices/{id}/route-intercompany`` and the underlying
``services.intercompany.route_intercompany_invoice``:

  - routing creates a mirror payable under the counterparty entity with the
    exact Decimal amount and a bidirectional ``intercompany_mirror_id`` link
  - idempotency: a second call returns the SAME mirror and the invoice count
    is unchanged (no duplicate payable)
  - self-billing (counterparty == own entity) is rejected (400)
  - RBAC: an ap_clerk is 403

Runs against the opt-in ``realdb`` fixture (skips without ``pnpm db:up``).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus


async def _default_entity_id(mk) -> uuid.UUID:
    async with mk() as s:
        return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _make_entity(mk, org_id, *, name: str, slug: str) -> uuid.UUID:
    eid = uuid.uuid4()
    async with mk() as s:
        s.add(Entity(id=eid, organization_id=org_id, name=name, slug=slug))
        await s.commit()
    return eid


async def _seed_invoice(
    mk,
    org_id,
    *,
    entity_id: uuid.UUID,
    amount: str = "1234.56",
    number: str = "IC-ORIGIN-1",
) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                entity_id=entity_id,
                invoice_number=number,
                vendor_name="Intercompany Vendor",
                amount=Decimal(amount),
                currency="USD",
                status=InvoiceStatus.approved,
            )
        )
        await s.commit()
    return inv_id


# ---------------------------------------------------------------------------
# Happy path — mirror created under counterparty, exact amount, linked both ways
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_creates_linked_mirror_under_counterparty(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    origin_entity = await _default_entity_id(mk)
    counterparty = await _make_entity(mk, info.org_id, name="Subsidiary B", slug="ic-sub-b")
    origin_id = await _seed_invoice(mk, info.org_id, entity_id=origin_entity, amount="1234.56")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/invoices/{origin_id}/route-intercompany",
            json={"counterparty_entity_id": str(counterparty)},
        )
    assert resp.status_code == 200, resp.text
    mirror_json = resp.json()
    mirror_id = uuid.UUID(mirror_json["id"])
    assert mirror_id != origin_id
    # Mirror's counterparty points back at the origin's entity.
    assert mirror_json["counterparty_entity_id"] == str(origin_entity)
    assert mirror_json["intercompany_mirror_id"] == str(origin_id)

    # Read both rows back to prove durability + exact amount + bidirectional link.
    async with mk() as s:
        mirror = (await s.execute(select(Invoice).where(Invoice.id == mirror_id))).scalar_one()
        origin = (await s.execute(select(Invoice).where(Invoice.id == origin_id))).scalar_one()

    # Mirror lives under the counterparty entity, with the EXACT Decimal amount.
    assert mirror.entity_id == counterparty
    assert mirror.amount == Decimal("1234.56")
    assert mirror.currency == "USD"
    assert mirror.status == InvoiceStatus.new
    assert mirror.invoice_number == "IC-IC-ORIGIN-1"
    # Bidirectional link.
    assert mirror.intercompany_mirror_id == origin_id
    assert mirror.counterparty_entity_id == origin_entity
    assert origin.intercompany_mirror_id == mirror_id


# ---------------------------------------------------------------------------
# Idempotency — second call returns the same mirror, no duplicate payable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_is_idempotent(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    origin_entity = await _default_entity_id(mk)
    counterparty = await _make_entity(mk, info.org_id, name="Subsidiary C", slug="ic-sub-c")
    origin_id = await _seed_invoice(
        mk, info.org_id, entity_id=origin_entity, number="IC-IDEMPOTENT-1"
    )

    async def _count() -> int:
        async with mk() as s:
            return (await s.execute(select(func.count(Invoice.id)))).scalar_one()

    before = await _count()

    async with realdb.client(key="a", role="ap_manager") as c:
        first = await c.post(
            f"/api/invoices/{origin_id}/route-intercompany",
            json={"counterparty_entity_id": str(counterparty)},
        )
        assert first.status_code == 200, first.text
        second = await c.post(
            f"/api/invoices/{origin_id}/route-intercompany",
            json={"counterparty_entity_id": str(counterparty)},
        )
        assert second.status_code == 200, second.text

    # Same mirror returned both times.
    assert first.json()["id"] == second.json()["id"]
    # Exactly one mirror created (origin + 1 mirror = before + 1).
    assert await _count() == before + 1


# ---------------------------------------------------------------------------
# Self-billing is rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_self_billing_is_rejected(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    origin_entity = await _default_entity_id(mk)
    origin_id = await _seed_invoice(mk, info.org_id, entity_id=origin_entity, number="IC-SELF-1")

    async def _count() -> int:
        async with mk() as s:
            return (await s.execute(select(func.count(Invoice.id)))).scalar_one()

    before = await _count()

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            f"/api/invoices/{origin_id}/route-intercompany",
            json={"counterparty_entity_id": str(origin_entity)},
        )
    assert resp.status_code == 400, resp.text
    # No mirror created.
    assert await _count() == before


# ---------------------------------------------------------------------------
# RBAC — ap_clerk cannot route
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ap_clerk_forbidden(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    origin_entity = await _default_entity_id(mk)
    counterparty = await _make_entity(mk, info.org_id, name="Subsidiary D", slug="ic-sub-d")
    origin_id = await _seed_invoice(mk, info.org_id, entity_id=origin_entity, number="IC-RBAC-1")

    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            f"/api/invoices/{origin_id}/route-intercompany",
            json={"counterparty_entity_id": str(counterparty)},
        )
    assert resp.status_code == 403, resp.text
