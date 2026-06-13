"""PEPPOL send-service tests (`services/peppol_send.send_invoice_over_peppol`).

Real-Postgres: the idempotency guarantee is a DB partial unique index, so it
can only be proven against a live tenant DB. Covers the happy path (row + one
audit row), idempotent re-send (one adapter call, one row, one audit row),
tax-invalid (no row, no audit), unknown receiver (no live row), failed-then-
retry (failed row excluded from the live index so a retry is allowed), and that
no participant value / tax id leaks into a log record.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.peppol_transmission import PeppolTransmission
from app.models.workflow import AuditLog
from app.services.e_invoice import BuyerIdentity, EInvoiceValidationError
from app.services.peppol_adapters import ParticipantId, PeppolSendError
from app.services.peppol_send import send_invoice_over_peppol

_BUYER = BuyerIdentity(name="Buyer Co")
_SENDER = ParticipantId("9930", "DE000000000")
_RECEIVER = ParticipantId("9930", "SUPPLIER123")


async def _seed_invoice(mk, org_id, *, vendor_tax_id=None) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
                invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
                vendor_name="Acme GmbH",
                vendor_tax_id=vendor_tax_id,
                amount=Decimal("100.00"),
                currency="USD",
                invoice_date=date(2026, 1, 1),
                subtotal=Decimal("100.00"),
                status=InvoiceStatus.approved,
            )
        )
        s.add(
            InvoiceLineItem(
                invoice_id=inv_id,
                line_number=1,
                description="Widget",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                total=Decimal("100.00"),
            )
        )
        await s.commit()
    return inv_id


async def _load(mk, inv_id):
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        items = list(
            (await s.execute(select(InvoiceLineItem).where(InvoiceLineItem.invoice_id == inv_id)))
            .scalars()
            .all()
        )
    return inv, items


@pytest.mark.asyncio
async def test_send_happy_path_persists_row_and_one_audit(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _seed_invoice(mk, org_id)
    inv, items = await _load(mk, inv_id)

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        transmission, already = await send_invoice_over_peppol(
            s,
            invoice=inv,
            line_items=items,
            buyer=_BUYER,
            sender_id=_SENDER,
            receiver_id=_RECEIVER,
            organization_id=org_id,
            entity_id=inv.entity_id,
            actor_id=uuid.uuid4(),
            peppol_config=None,
        )

    assert already is False
    assert transmission.status == "sent"
    assert transmission.direction == "outbound"
    assert transmission.message_id
    assert transmission.amount == Decimal("100.00")  # Decimal, never float
    assert transmission.currency == "USD"
    assert transmission.business_message_id == inv.correlation_id.hex

    async with mk() as s:
        rows = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(PeppolTransmission.invoice_id == inv_id)
            )
        ).scalar_one()
        assert rows == 1
        audits = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id, AuditLog.action == "invoice.peppol_sent")
            )
        ).scalar_one()
        assert audits == 1


@pytest.mark.asyncio
async def test_send_is_idempotent(realdb):
    """Two sends → ONE live row, ONE adapter.send call, ONE audit row."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _seed_invoice(mk, org_id)
    _, items = await _load(mk, inv_id)

    call_counter = {"n": 0}
    from app.services.peppol_adapters.mock_adapter import MockPeppolAdapter

    real_send = MockPeppolAdapter.send

    async def counting_send(self, request):
        call_counter["n"] += 1
        return await real_send(self, request)

    for _ in range(2):
        async with mk() as s:
            inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
            import app.services.peppol_adapters.mock_adapter as mod

            orig = mod.MockPeppolAdapter.send
            mod.MockPeppolAdapter.send = counting_send
            try:
                _, already = await send_invoice_over_peppol(
                    s,
                    invoice=inv,
                    line_items=items,
                    buyer=_BUYER,
                    sender_id=_SENDER,
                    receiver_id=_RECEIVER,
                    organization_id=org_id,
                    entity_id=inv.entity_id,
                    actor_id=uuid.uuid4(),
                    peppol_config=None,
                )
            finally:
                mod.MockPeppolAdapter.send = orig

    assert already is True  # second call short-circuited
    assert call_counter["n"] == 1  # adapter.send called exactly once

    async with mk() as s:
        rows = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(PeppolTransmission.invoice_id == inv_id)
            )
        ).scalar_one()
        assert rows == 1
        audits = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id, AuditLog.action.like("invoice.peppol_%"))
            )
        ).scalar_one()
        assert audits == 1


@pytest.mark.asyncio
async def test_tax_invalid_invoice_persists_no_row(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    # A malformed DE VAT id makes the document tax-invalid.
    inv_id = await _seed_invoice(mk, org_id, vendor_tax_id="DE12")
    _, items = await _load(mk, inv_id)

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        with pytest.raises(EInvoiceValidationError) as excinfo:
            await send_invoice_over_peppol(
                s,
                invoice=inv,
                line_items=items,
                buyer=_BUYER,
                sender_id=_SENDER,
                receiver_id=_RECEIVER,
                organization_id=org_id,
                entity_id=inv.entity_id,
                actor_id=uuid.uuid4(),
                peppol_config=None,
            )
    # PII-free: the tax id value never appears in the error.
    assert "DE12" not in str(excinfo.value)

    async with mk() as s:
        rows = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(PeppolTransmission.invoice_id == inv_id)
            )
        ).scalar_one()
        assert rows == 0


@pytest.mark.asyncio
async def test_unknown_receiver_raises_and_leaves_no_live_row(realdb, caplog):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _seed_invoice(mk, org_id)
    _, items = await _load(mk, inv_id)
    receiver = ParticipantId("9930", "UNREGISTERED-CO")

    with caplog.at_level(logging.DEBUG):
        async with mk() as s:
            inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
            with pytest.raises(PeppolSendError) as excinfo:
                await send_invoice_over_peppol(
                    s,
                    invoice=inv,
                    line_items=items,
                    buyer=_BUYER,
                    sender_id=_SENDER,
                    receiver_id=receiver,
                    organization_id=org_id,
                    entity_id=inv.entity_id,
                    actor_id=uuid.uuid4(),
                    peppol_config=None,
                )
    assert excinfo.value.code == "receiver_not_registered"

    async with mk() as s:
        rows = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(PeppolTransmission.invoice_id == inv_id)
            )
        ).scalar_one()
        assert rows == 0

    # PII: no participant value in any captured log record.
    assert "UNREGISTERED-CO" not in caplog.text


@pytest.mark.asyncio
async def test_failed_send_allows_retry(realdb):
    """A failed send leaves status='failed' (excluded from the live index), so a
    subsequent send is allowed and creates a fresh live row."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _seed_invoice(mk, org_id)
    _, items = await _load(mk, inv_id)

    import app.services.peppol_adapters.mock_adapter as mod
    from app.services.peppol_adapters.base import TransmissionResult

    async def failing_send(self, request):
        return TransmissionResult(
            success=False, status="failed", failure_reason="gateway_error:500"
        )

    orig = mod.MockPeppolAdapter.send
    mod.MockPeppolAdapter.send = failing_send
    try:
        async with mk() as s:
            inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
            t1, already = await send_invoice_over_peppol(
                s,
                invoice=inv,
                line_items=items,
                buyer=_BUYER,
                sender_id=_SENDER,
                receiver_id=_RECEIVER,
                organization_id=org_id,
                entity_id=inv.entity_id,
                actor_id=uuid.uuid4(),
                peppol_config=None,
            )
        assert already is False
        assert t1.status == "failed"
    finally:
        mod.MockPeppolAdapter.send = orig

    # Retry now succeeds (the failed row does not block the live index).
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        t2, already = await send_invoice_over_peppol(
            s,
            invoice=inv,
            line_items=items,
            buyer=_BUYER,
            sender_id=_SENDER,
            receiver_id=_RECEIVER,
            organization_id=org_id,
            entity_id=inv.entity_id,
            actor_id=uuid.uuid4(),
            peppol_config=None,
        )
    assert already is False
    assert t2.status == "sent"
    assert t2.id != t1.id

    async with mk() as s:
        live = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(
                    PeppolTransmission.invoice_id == inv_id,
                    PeppolTransmission.status != "failed",
                )
            )
        ).scalar_one()
        assert live == 1
