"""A live virtual card is a claim on its invoice — no other rail may also pay it.

`POST /api/cards/generate` mints a spendable card for the full invoice amount
and, unlike the `virtual_card` leg of `execute_payment_run`, books **no**
`Payment` row and leaves the invoice `approved`. Every "is this invoice already
being paid" gate keys on `Payment`:

* `uq_payments_one_live_per_invoice` (and its `_live_payment_invoice_numbers`
  pre-check) counts payment rows;
* `/payments/queue` excludes an invoice only once it has a `completed` payment.

So a directly-minted card left the invoice fully payable by ACH. The vendor
held a live card for the face amount **and** received a wire — the money went
out twice, with nothing in either audit trail contradicting the other.

The run leg already handles the *converge* case correctly when the second
payment is itself a card (`find_live_card_for_invoice` → `card_settlement_block`
→ link + settle), so the gate here is deliberately method-aware: a card claim
blocks every rail EXCEPT `virtual_card`. Cancelling the card releases the claim
(`uq_virtual_cards_one_live_per_invoice`'s own predicate is `status <>
'cancelled'`, and this reuses it).

Runs against the opt-in `realdb` fixture (skips without `pnpm db:up`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor
from app.models.virtual_card import VirtualCard

pytestmark = pytest.mark.asyncio


async def _seed(mk, org_id, *, number: str, amount: Decimal) -> tuple[str, str]:
    async with mk() as s:
        vendor = Vendor(organization_id=org_id, name="Card Claim Vendor")
        s.add(vendor)
        await s.flush()
        inv = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name=vendor.name,
            vendor_id=vendor.id,
            amount=amount,
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id), str(vendor.id)


async def _mint_card(mk, org_id, invoice_id: str, *, status: str = "created") -> None:
    """A card occupying the invoice's live-card slot, with no payment behind it —
    exactly what `POST /api/cards/generate` persists."""
    import uuid as _uuid

    async with mk() as s:
        s.add(
            VirtualCard(
                organization_id=org_id,
                invoice_id=_uuid.UUID(invoice_id),
                card_provider="mock",
                provider_card_id=f"mock_{_uuid.uuid4().hex[:12]}",
                last_four="4242",
                amount_limit=Decimal("1000.00"),
                currency="USD",
                status=status,
            )
        )
        await s.commit()


async def test_ach_run_refuses_an_invoice_already_holding_a_live_card(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    invoice_id, _ = await _seed(mk, info.org_id, number="CARDCLM-1", amount=Decimal("1000.00"))
    await _mint_card(mk, info.org_id, invoice_id)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": invoice_id, "method": "ach"}]},
        )

    assert resp.status_code == 409, resp.text
    assert "CARDCLM-1" in resp.json()["detail"]
    async with mk() as s:
        booked = (
            (await s.execute(select(Payment).where(Payment.invoice_id == invoice_id)))
            .scalars()
            .all()
        )
    assert booked == []


async def test_a_spent_card_blocks_just_as_hard(realdb):
    """A `charged` card means the money already moved on that rail — the most
    important case to refuse, and the one `card_settlement_block` alone can't
    reach because an ACH run never consults the card at all."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    invoice_id, _ = await _seed(mk, info.org_id, number="CARDCLM-2", amount=Decimal("1000.00"))
    await _mint_card(mk, info.org_id, invoice_id, status="charged")

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": invoice_id, "method": "wire"}]},
        )
    assert resp.status_code == 409, resp.text


async def test_virtual_card_run_is_not_blocked(realdb):
    """The documented converge path must survive: a `virtual_card` run over an
    invoice that already holds a live card links and settles against that card
    (`api/payments._execute_single_payment`), so the claim gate must not refuse
    it."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    invoice_id, _ = await _seed(mk, info.org_id, number="CARDCLM-3", amount=Decimal("1000.00"))
    await _mint_card(mk, info.org_id, invoice_id)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": invoice_id, "method": "virtual_card"}]},
        )
    assert resp.status_code == 201, resp.text


async def test_a_cancelled_card_releases_the_claim(realdb):
    """Cancelling the card is the exit — the same predicate
    `uq_virtual_cards_one_live_per_invoice` uses (`status <> 'cancelled'`), so
    an invoice whose card was killed is payable by any rail again."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    invoice_id, _ = await _seed(mk, info.org_id, number="CARDCLM-4", amount=Decimal("1000.00"))
    await _mint_card(mk, info.org_id, invoice_id, status="cancelled")

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": invoice_id, "method": "ach"}]},
        )
    assert resp.status_code == 201, resp.text


async def test_standalone_payment_refuses_a_card_claimed_invoice(realdb):
    """`POST /api/payments` books money on the same terms as a run, so it runs
    the same gate — otherwise the run's refusal is walked around by posting
    here instead (the same reasoning as the financial-integrity exception
    gate)."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    invoice_id, _ = await _seed(mk, info.org_id, number="CARDCLM-5", amount=Decimal("1000.00"))
    await _mint_card(mk, info.org_id, invoice_id)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments",
            json={"invoice_id": invoice_id, "amount": "1000.00", "method": "ach"},
        )
    assert resp.status_code == 409, resp.text
