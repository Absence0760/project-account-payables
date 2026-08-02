"""`DELETE /api/invoices/{id}` must cascade through every NO ACTION FK.

`_delete_invoice_cascade` used to clear only six tables (exceptions,
payments, payment_schedules, workflow_instances, invoice_extraction_results,
invoice_line_items). Every other table with a `NO ACTION` FK to invoices
(virtual_cards, agent_decisions, peppol_transmissions, credit_memos,
discount_offers, supplier_chat_threads, vendor_statement_recon_lines) was
missing — deleting an invoice that had one of those rows raised an unhandled
IntegrityError (500) instead of a clean delete. Grandchildren of those
tables (card_reveal_tokens/card_rebates under virtual_cards,
supplier_chat_messages under supplier_chat_threads) needed clearing first.
corporate_card_transactions is a separate case: it's an independently
imported bank-statement feed reconciled TO a virtual card, not owned by the
invoice, so it's unlinked (FK set NULL) rather than deleted.

Found live by exploratory persona-driven testing (approver persona).
Runs against the opt-in `realdb` fixture (skips without `pnpm db:up`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.credit_memo import CreditMemo
from app.models.expense import CorporateCardTransaction
from app.models.invoice import Invoice, InvoiceStatus
from app.models.supplier_chat import (
    ChatAuthorRole,
    ChatThreadStatus,
    SupplierChatMessage,
    SupplierChatThread,
)
from app.models.vendor import Vendor
from app.models.virtual_card import CardRebate, CardRevealToken, VirtualCard

pytestmark = pytest.mark.asyncio

TENANT = "a"


async def _seed_invoice(mk, org_id, *, number: str) -> str:
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            invoice_number=number,
            vendor_name="Delete Cascade Vendor",
            amount=Decimal("500.00"),
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


async def test_delete_invoice_with_virtual_card_succeeds(realdb):
    """The exact live repro: an approved invoice with an auto-issued
    virtual card (plus its own reveal token + rebate) must delete cleanly,
    not 500."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_invoice(mk, org_id, number="DELCASC-CARD")

    async with mk() as s:
        card = VirtualCard(
            organization_id=org_id,
            invoice_id=uuid.UUID(invoice_id),
            card_provider="mock",
            provider_card_id=f"card_{uuid.uuid4().hex[:8]}",
            amount_limit=Decimal("500.00"),
            status="charged",
        )
        s.add(card)
        await s.flush()
        s.add(
            CardRevealToken(
                token_hash="a" * 64,
                card_id=card.id,
                organization_id=org_id,
                expires_at=datetime.now(UTC) + timedelta(days=7),
            )
        )
        s.add(
            CardRebate(
                organization_id=org_id,
                virtual_card_id=card.id,
                amount=Decimal("7.50"),
                rate=Decimal("0.0150"),
            )
        )
        s.add(
            CorporateCardTransaction(
                organization_id=org_id,
                virtual_card_id=card.id,
                txn_date=date.today(),
                amount=Decimal("500.00"),
            )
        )
        await s.commit()
        card_id = card.id

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.delete(f"/api/invoices/{invoice_id}")
    assert resp.status_code == 204, resp.text

    async with mk() as s:
        assert (
            await s.execute(select(Invoice).where(Invoice.id == uuid.UUID(invoice_id)))
        ).first() is None
        assert (
            await s.execute(select(VirtualCard).where(VirtualCard.id == card_id))
        ).first() is None
        assert (
            await s.execute(select(CardRevealToken).where(CardRevealToken.card_id == card_id))
        ).first() is None
        assert (
            await s.execute(select(CardRebate).where(CardRebate.virtual_card_id == card_id))
        ).first() is None
        # Unlinked, not deleted — the bank-statement feed row survives.
        txn = (
            await s.execute(
                select(CorporateCardTransaction).where(
                    CorporateCardTransaction.organization_id == org_id
                )
            )
        ).scalar_one()
        assert txn.virtual_card_id is None


async def test_delete_invoice_with_applied_credit_memo_succeeds(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_invoice(mk, org_id, number="DELCASC-MEMO")

    async with mk() as s:
        vendor = Vendor(organization_id=org_id, name="Delete Cascade Vendor")
        s.add(vendor)
        await s.flush()
        s.add(
            CreditMemo(
                memo_number="CM-DELCASC",
                vendor_id=vendor.id,
                invoice_id=uuid.UUID(invoice_id),
                amount=Decimal("50.00"),
                status="applied",
                organization_id=org_id,
            )
        )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.delete(f"/api/invoices/{invoice_id}")
    assert resp.status_code == 204, resp.text

    async with mk() as s:
        assert (
            await s.execute(select(CreditMemo).where(CreditMemo.memo_number == "CM-DELCASC"))
        ).first() is None


async def test_delete_invoice_with_supplier_chat_thread_succeeds(realdb):
    """Grandchild-of-grandchild ordering: messages must clear before their
    thread, and the thread before the invoice."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_invoice(mk, org_id, number="DELCASC-CHAT")

    async with mk() as s:
        thread = SupplierChatThread(
            organization_id=org_id,
            invoice_id=uuid.UUID(invoice_id),
            status=ChatThreadStatus.open,
        )
        s.add(thread)
        await s.flush()
        s.add(
            SupplierChatMessage(
                thread_id=thread.id,
                author_role=ChatAuthorRole.ap_team,
                author_user_id=None,
                body="test message",
            )
        )
        await s.commit()
        thread_id = thread.id

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.delete(f"/api/invoices/{invoice_id}")
    assert resp.status_code == 204, resp.text

    async with mk() as s:
        assert (
            await s.execute(select(SupplierChatThread).where(SupplierChatThread.id == thread_id))
        ).first() is None
        assert (
            await s.execute(
                select(SupplierChatMessage).where(SupplierChatMessage.thread_id == thread_id)
            )
        ).first() is None
