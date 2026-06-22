"""Virtual-card issuance idempotency — at most one LIVE card per invoice.

Card issuance mints a real provider card, so a retried request must not
double-issue. The partial unique index uq_virtual_cards_one_live_per_invoice
(migration 0067) is the DB-level backstop: a second non-cancelled card for the
same invoice is rejected, while a cancel-then-reissue still works.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.invoice import Invoice, InvoiceStatus
from app.models.virtual_card import VirtualCard

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_invoice(mk, org_id) -> tuple[uuid.UUID, uuid.UUID]:
    async with mk() as s:
        ent = await _default_entity_id(s)
        inv = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"CARD-{uuid.uuid4().hex[:8]}",
            vendor_name="Card Vendor",
            amount=Decimal("100.00"),
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        return inv.id, ent


def _card(invoice_id, org_id, ent, *, provider_card_id, status="created") -> VirtualCard:
    return VirtualCard(
        invoice_id=invoice_id,
        organization_id=org_id,
        entity_id=ent,
        card_provider="mock",
        provider_card_id=provider_card_id,
        amount_limit=Decimal("100.00"),
        currency="USD",
        status=status,
    )


@pytest.mark.asyncio
async def test_second_live_card_for_invoice_is_rejected(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv_id, ent = await _seed_invoice(mk, org_id)

    async with mk() as s:
        s.add(_card(inv_id, org_id, ent, provider_card_id="card_1"))
        await s.commit()

    # A second LIVE card for the same invoice violates the partial unique index.
    async with mk() as s:
        s.add(_card(inv_id, org_id, ent, provider_card_id="card_2"))
        with pytest.raises(IntegrityError):
            await s.commit()


@pytest.mark.asyncio
async def test_cancelled_card_does_not_block_reissue(realdb):
    """Cancel-then-reissue is legitimate — a cancelled card is excluded from the
    partial index, so a fresh live card for the same invoice is allowed."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv_id, ent = await _seed_invoice(mk, org_id)

    async with mk() as s:
        s.add(_card(inv_id, org_id, ent, provider_card_id="card_old", status="cancelled"))
        await s.commit()

    async with mk() as s:
        s.add(_card(inv_id, org_id, ent, provider_card_id="card_new", status="created"))
        await s.commit()  # must NOT raise

    async with mk() as s:
        live = (
            (
                await s.execute(
                    select(VirtualCard).where(
                        VirtualCard.invoice_id == inv_id,
                        VirtualCard.status != "cancelled",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(live) == 1
    assert live[0].provider_card_id == "card_new"
