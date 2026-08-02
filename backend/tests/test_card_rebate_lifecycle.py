"""`CardRebate.status` lifecycle transitions — `pending` -> `confirmed` -> `paid_out`.

Nothing transitioned a rebate's status past its `pending` default: the model
documents `pending`/`confirmed`/`paid_out` (`backend/docs/virtual-cards.md`),
but real rebate confirmation/payout from Lithic/Nium arrives out-of-band (a
periodic statement, not a webhook event we already ingest), so there was no
code path to advance it. Found by exploratory persona-driven testing
(card-processor persona); recorded as a "minor / out of scope" gap, fixed
here with two admin-driven confirmation endpoints.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.virtual_card import CardRebate, VirtualCard

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_rebate(mk, org_id, *, status: str = "pending") -> uuid.UUID:
    async with mk() as s:
        ent = await _default_entity_id(s)
        inv = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"REB-{uuid.uuid4().hex[:8]}",
            vendor_name="Rebate Vendor",
            amount=Decimal("100.00"),
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.flush()
        card = VirtualCard(
            invoice_id=inv.id,
            organization_id=org_id,
            entity_id=ent,
            card_provider="mock",
            provider_card_id=f"card_{uuid.uuid4().hex[:10]}",
            amount_limit=Decimal("100.00"),
            amount_charged=Decimal("100.00"),
            currency="USD",
            status="completed",
        )
        s.add(card)
        await s.flush()
        rebate = CardRebate(
            virtual_card_id=card.id,
            amount=Decimal("1.50"),
            rate=Decimal("0.0150"),
            status=status,
            period="2026-07",
            organization_id=org_id,
        )
        s.add(rebate)
        await s.commit()
        return rebate.id


@pytest.mark.asyncio
async def test_confirm_moves_pending_to_confirmed(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    rebate_id = await _seed_rebate(mk, org_id, status="pending")

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(f"/api/cards/rebates/{rebate_id}/confirm")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "confirmed"

    async with mk() as s:
        status = (
            await s.execute(select(CardRebate.status).where(CardRebate.id == rebate_id))
        ).scalar_one()
    assert status == "confirmed"


@pytest.mark.asyncio
async def test_confirm_is_409_when_not_pending(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    rebate_id = await _seed_rebate(mk, org_id, status="confirmed")

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(f"/api/cards/rebates/{rebate_id}/confirm")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_mark_paid_moves_confirmed_to_paid_out(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    rebate_id = await _seed_rebate(mk, org_id, status="confirmed")

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(f"/api/cards/rebates/{rebate_id}/mark-paid")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "paid_out"

    async with mk() as s:
        status = (
            await s.execute(select(CardRebate.status).where(CardRebate.id == rebate_id))
        ).scalar_one()
    assert status == "paid_out"


@pytest.mark.asyncio
async def test_mark_paid_refuses_to_skip_confirmed(realdb):
    """A rebate can't jump straight from pending to paid_out — confirmation is
    a required step, not a formality."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    rebate_id = await _seed_rebate(mk, org_id, status="pending")

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(f"/api/cards/rebates/{rebate_id}/mark-paid")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_confirm_404_on_unknown_rebate(realdb):
    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(f"/api/cards/rebates/{uuid.uuid4()}/confirm")
    assert resp.status_code == 404
