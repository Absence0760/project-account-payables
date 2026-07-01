"""One rebate per virtual card — DB-level backstop (migration 0069).

A single-use card settles once → exactly one rebate. The webhook already guards
on ``card.status == "charged"`` + event-id dedup, but the unique index
``uq_card_rebates_virtual_card`` is the hard last line against a double-rebate
under a race / Redis-outage. The settlement branch in ``api/cards.py`` inserts
the rebate inside a savepoint, so a duplicate is silently skipped WITHOUT
aborting the card completion + audit row.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models.invoice import Invoice, InvoiceStatus
from app.models.virtual_card import CardRebate, VirtualCard

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_card(mk, org_id) -> tuple[uuid.UUID, uuid.UUID]:
    """Create an approved invoice + a charged virtual card; return (card_id, ent)."""
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
            status="charged",
        )
        s.add(card)
        await s.commit()
        return card.id, ent


def _rebate(card_id, org_id) -> CardRebate:
    return CardRebate(
        virtual_card_id=card_id,
        amount=Decimal("1.00"),
        rate=Decimal("0.0100"),
        status="pending",
        period="2026-07",
        organization_id=org_id,
    )


@pytest.mark.asyncio
async def test_second_rebate_for_card_is_rejected(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    card_id, _ent = await _seed_card(mk, org_id)

    async with mk() as s:
        s.add(_rebate(card_id, org_id))
        await s.commit()

    # A second rebate for the same card violates uq_card_rebates_virtual_card.
    async with mk() as s:
        s.add(_rebate(card_id, org_id))
        with pytest.raises(IntegrityError):
            await s.commit()


@pytest.mark.asyncio
async def test_duplicate_rebate_in_savepoint_skipped_transition_survives(realdb):
    """Mirrors the api/cards.py settlement branch: a duplicate rebate insert
    inside a savepoint is skipped, while the card completion still commits and
    only one rebate exists."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    card_id, _ent = await _seed_card(mk, org_id)

    # First settlement already recorded a rebate.
    async with mk() as s:
        s.add(_rebate(card_id, org_id))
        await s.commit()

    # Second settlement arrives (race / Redis-outage). Apply the exact pattern:
    # flip the card to completed, try the rebate insert in a savepoint (rejected
    # by the unique index), and still commit the money-state transition.
    async with mk() as s:
        card = (await s.execute(select(VirtualCard).where(VirtualCard.id == card_id))).scalar_one()
        card.status = "completed"
        rebate_created = True
        try:
            async with s.begin_nested():
                s.add(_rebate(card_id, org_id))
                await s.flush()
        except IntegrityError:
            rebate_created = False
        await s.commit()

    assert rebate_created is False

    async with mk() as s:
        count = (
            await s.execute(
                select(func.count())
                .select_from(CardRebate)
                .where(CardRebate.virtual_card_id == card_id)
            )
        ).scalar()
        status = (
            await s.execute(select(VirtualCard.status).where(VirtualCard.id == card_id))
        ).scalar_one()

    assert count == 1, "duplicate rebate must not be created"
    assert status == "completed", "card completion must survive the skipped duplicate rebate"
