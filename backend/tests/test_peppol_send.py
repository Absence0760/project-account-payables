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

# The buyer is US. PEPPOL BIS Billing 3.0 requires the buyer's country code
# (BR-11) and name; the buyer's electronic address comes from `sender_id`.
_BUYER = BuyerIdentity(name="Buyer Co", country_code="DE")
_SENDER = ParticipantId("9930", "DE000000000")
_RECEIVER = ParticipantId("9930", "SUPPLIER123")


async def _seed_invoice(mk, org_id, *, vendor_tax_id="DE123456789") -> uuid.UUID:
    """A BIS Billing 3.0-conformant invoice.

    `send_invoice_over_peppol` transmits under a doc-type id that ASSERTS BIS
    3.0, so it refuses a document that provably does not meet the profile. The
    seller's country is derived from its VAT-id prefix, hence the DE id; the tax
    figures give the VAT breakdown BR-CO-14 requires, and the due date satisfies
    BR-CO-25 (an invoice with an amount due states when, or on what terms).
    """
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
                amount=Decimal("119.00"),
                currency="EUR",
                invoice_date=date(2026, 1, 1),
                due_date=date(2026, 1, 31),
                subtotal=Decimal("100.00"),
                tax_amount=Decimal("19.00"),
                tax_rate=Decimal("19.00"),
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
                tax=Decimal("19.00"),
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
    assert transmission.amount == Decimal("119.00")  # Decimal, never float
    assert transmission.currency == "EUR"
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
async def test_send_idempotent_concurrent_collision(realdb, monkeypatch):
    """Exercise the DB-level IntegrityError race branch (peppol_send.py 134-141).

    The authoritative idempotency guarantee is the partial unique index, not the
    application-level short-circuit. Here a live row already exists, but we force
    the send past the short-circuit (patch `_select_live_outbound` to return None
    once) so the flush hits the real index → IntegrityError → rollback → reselect
    returns the committed racer with `already_sent=True`, and the adapter is never
    called.
    """
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _seed_invoice(mk, org_id)
    _, items = await _load(mk, inv_id)

    # Commit a live 'sending' row first — this is the row the racer should win.
    existing_id = uuid.uuid4()
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        s.add(
            PeppolTransmission(
                id=existing_id,
                invoice_id=inv_id,
                direction="outbound",
                participant_scheme=_RECEIVER.scheme,
                participant_value=_RECEIVER.value,
                sender_scheme=_SENDER.scheme,
                sender_value=_SENDER.value,
                doc_type_id="dt",
                process_id="pr",
                business_message_id=inv.correlation_id.hex,
                status="sending",
                provider="mock",
                amount=Decimal("100.00"),
                currency="USD",
                organization_id=org_id,
                entity_id=inv.entity_id,
            )
        )
        await s.commit()

    import app.services.peppol_adapters.mock_adapter as mod
    import app.services.peppol_send as send_mod

    real_select = send_mod._select_live_outbound
    state = {"first": True}

    async def select_skip_once(db, invoice_id):
        # First call (the short-circuit) returns None to force the insert path;
        # the rollback-reselect call uses the real selector and finds the racer.
        if state["first"]:
            state["first"] = False
            return None
        return await real_select(db, invoice_id)

    monkeypatch.setattr(send_mod, "_select_live_outbound", select_skip_once)

    send_calls = {"n": 0}
    orig_send = mod.MockPeppolAdapter.send

    async def counting_send(self, request):
        send_calls["n"] += 1
        return await orig_send(self, request)

    mod.MockPeppolAdapter.send = counting_send
    try:
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
    finally:
        mod.MockPeppolAdapter.send = orig_send

    assert already is True
    assert transmission.id == existing_id  # the committed racer, not a new row
    assert send_calls["n"] == 0  # the network was never touched

    async with mk() as s:
        rows = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(PeppolTransmission.invoice_id == inv_id)
            )
        ).scalar_one()
        assert rows == 1  # the IntegrityError rolled back the second insert


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
        # The failing AP still returns a non-NULL message_id — the send service
        # must NOT persist it on the failed row, or the supported retry (which
        # reuses the same business_message_id, so a real AP echoes the same
        # MessageId) would collide on uq_peppol_message_id → IntegrityError.
        return TransmissionResult(
            success=False,
            status="failed",
            message_id="ap-msg-same",
            failure_reason="gateway_error:500",
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
        assert t1.message_id is None  # never persisted on a failed row
    finally:
        mod.MockPeppolAdapter.send = orig

    # The failure branch wrote exactly one PII-free audit row.
    async with mk() as s:
        failed_audits = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.entity_id == inv_id,
                    AuditLog.action == "invoice.peppol_send_failed",
                )
            )
        ).scalar_one()
        assert failed_audits == 1

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


@pytest.mark.asyncio
async def test_receiver_doctype_unsupported_refuses_before_persisting(realdb):
    """A receiver registered for a DIFFERENT doc type is refused at the SMP step,
    before any live row or audit row is written."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _seed_invoice(mk, org_id)
    _, items = await _load(mk, inv_id)

    import app.services.peppol_adapters.mock_adapter as mod
    from app.services.peppol_adapters.base import ParticipantCapability

    async def resolve_other_doctype(self, participant_id):
        # Registered, but only accepts an Order doc type — not BIS Billing.
        return ParticipantCapability(
            participant_id=participant_id,
            registered=True,
            access_point_url="https://ap.mock-peppol.invalid/as4",
            supported_doc_types=("urn:some:other:Order::Order##x::1.0",),
        )

    orig = mod.MockPeppolAdapter.resolve_participant
    mod.MockPeppolAdapter.resolve_participant = resolve_other_doctype
    try:
        async with mk() as s:
            inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
            with pytest.raises(PeppolSendError) as excinfo:
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
        assert excinfo.value.code == "receiver_doctype_unsupported"
    finally:
        mod.MockPeppolAdapter.resolve_participant = orig

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
async def test_send_refuses_a_non_bis3_conformant_document(realdb):
    """The transmission declares PEPPOL_BIS_BILLING_DOCTYPE, which ASSERTS
    EN 16931 / BIS Billing 3.0 conformance. A document that provably does not
    meet the profile must never leave the building under that claim.

    Here the seller's country cannot be established (a US EIN has no VAT-id
    country prefix), so BR-09 fails. The refusal is PII-free and, crucially,
    happens BEFORE any row is persisted or anything is transmitted.
    """
    from app.services.e_invoice import EInvoiceValidationError

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _seed_invoice(mk, org_id, vendor_tax_id=None)
    inv, items = await _load(mk, inv_id)

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

    assert "seller.country_code: missing" in str(excinfo.value)
    # PII-free: no party name, no id, no money in the message.
    assert "Acme GmbH" not in str(excinfo.value)

    # Nothing persisted — the refusal precedes the idempotency claim.
    async with mk() as s:
        rows = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(PeppolTransmission.invoice_id == inv_id)
            )
        ).scalar_one()
        assert rows == 0
