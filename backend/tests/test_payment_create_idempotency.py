"""Standalone POST /payments is idempotent: one LIVE payment per invoice.

`create_payment` used to book a fresh full-amount Payment on every call with no
idempotency guard and no audit row — so a retried / double-clicked / concurrent
POST booked a SECOND full-amount payment (a real double-pay) that no audit trail
distinguished from the first. The handler now:

  * locks the invoice FOR UPDATE and returns any existing LIVE payment instead
    of creating a duplicate (backed by the `uq_payments_one_live_per_invoice`
    partial unique index — migration 0074), and
  * writes a `payment.created` audit row on the booking (like every sibling
    money handler).

Terminal states (`voided`/`failed`/`cancelled`) don't count as live, so a
re-pay after a void still books a fresh payment.

All DB-backed via `realdb` (requires the dev Postgres; skips otherwise).
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.workflow import AuditLog

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_approved_invoice(mk, org_id, amount=Decimal("500.00")) -> uuid.UUID:
    async with mk() as s:
        ent = await _default_entity_id(s)
        inv = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"PAY-{uuid.uuid4().hex[:8]}",
            vendor_name="Pay Vendor",
            amount=amount,
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        return inv.id


async def _live_payment_count(mk, inv_id) -> int:
    async with mk() as s:
        return (
            await s.execute(
                select(func.count()).select_from(Payment).where(Payment.invoice_id == inv_id)
            )
        ).scalar_one()


async def _created_audit_count(mk, inv_id) -> int:
    async with mk() as s:
        return (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == "payment.created",
                    AuditLog.details["invoice_id"].astext == str(inv_id),
                )
            )
        ).scalar_one()


@pytest.mark.asyncio
async def test_double_post_books_one_payment_with_one_audit_row(realdb):
    """A retried / double-clicked POST yields ONE payment row + ONE audit row."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500.00"))

    async with realdb.client(key=TENANT, role="admin") as client:
        first = await client.post(
            "/api/payments",
            json={"invoice_id": str(inv_id), "amount": "500.00", "method": "ach"},
        )
        second = await client.post(
            "/api/payments",
            json={"invoice_id": str(inv_id), "amount": "500.00", "method": "ach"},
        )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    # The retry resolves to the SAME payment, not a new one.
    assert first.json()["id"] == second.json()["id"]

    # Exactly one Payment row, and exactly one append-only audit row for it.
    assert await _live_payment_count(mk, inv_id) == 1
    assert await _created_audit_count(mk, inv_id) == 1


@pytest.mark.asyncio
async def test_concurrent_posts_book_one_payment(realdb):
    """Two concurrent POSTs for the same invoice must book only ONE payment.

    The invoice FOR UPDATE lock serializes them; the loser sees the winner's
    committed payment and returns it. The `uq_payments_one_live_per_invoice`
    index is the DB backstop for any interleaving the lock can't cover.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_approved_invoice(mk, org_id, Decimal("750.00"))

    async with realdb.client(key=TENANT, role="admin") as client:
        payload = {"invoice_id": str(inv_id), "amount": "750.00", "method": "ach"}
        r1, r2 = await asyncio.gather(
            client.post("/api/payments", json=payload),
            client.post("/api/payments", json=payload),
        )

    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r1.json()["id"] == r2.json()["id"]

    # No duplicate: one live payment, one audit row.
    assert await _live_payment_count(mk, inv_id) == 1
    assert await _created_audit_count(mk, inv_id) == 1


@pytest.mark.asyncio
async def test_existing_live_payment_short_circuits(realdb):
    """A pre-existing LIVE payment is returned; no new row, no new audit."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500.00"))

    # Seed a live (pending) payment directly — no audit row.
    async with mk() as s:
        existing = Payment(
            invoice_id=inv_id,
            amount=Decimal("500.00"),
            method="ach",
            status="pending",
            correlation_id=uuid.uuid4(),
        )
        s.add(existing)
        await s.commit()
        existing_id = existing.id

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            "/api/payments",
            json={"invoice_id": str(inv_id), "amount": "500.00", "method": "ach"},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["id"] == str(existing_id)

    assert await _live_payment_count(mk, inv_id) == 1
    # The short-circuit path returns the existing payment; it never writes a
    # `payment.created` row (the seeded row had none).
    assert await _created_audit_count(mk, inv_id) == 0


@pytest.mark.asyncio
async def test_voided_payment_does_not_block_a_repay(realdb):
    """A voided (terminal) payment is not LIVE — a fresh payment can be booked."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500.00"))

    async with mk() as s:
        voided = Payment(
            invoice_id=inv_id,
            amount=Decimal("500.00"),
            method="ach",
            status="voided",
            correlation_id=uuid.uuid4(),
        )
        s.add(voided)
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            "/api/payments",
            json={"invoice_id": str(inv_id), "amount": "500.00", "method": "ach"},
        )
    assert resp.status_code == 201, resp.text

    # Two rows now: the voided one + the fresh live one. One new audit row.
    assert await _live_payment_count(mk, inv_id) == 2
    assert await _created_audit_count(mk, inv_id) == 1
    async with mk() as s:
        live = (
            (
                await s.execute(
                    select(Payment).where(
                        Payment.invoice_id == inv_id,
                        Payment.status.notin_(("voided", "failed", "cancelled")),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(live) == 1
        assert live[0].amount == Decimal("500.00")
