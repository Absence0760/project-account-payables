"""`POST /api/payments/runs` must net applied credit memos off what's paid.

Applying a credit memo (`/api/credit-memos`) is the entire point of the
feature — it should reduce what the vendor is actually paid. Before this
fix, `create_payment_run` hardcoded `Payment.amount = inv.amount` with no
`CreditMemo` lookup anywhere in the payment path: every guard around
*applying* a memo (vendor match, currency match, no over-application) was
solid, but none of it mattered because the payment run never read it back —
the vendor was still paid the full pre-credit amount. `credit_memos.py`'s
own over-application guard (`apply` refuses a memo that would exceed the
invoice's remaining creditable balance) is what guarantees the netted
amount here can never go negative.

Runs against the opt-in `realdb` fixture (skips without `pnpm db:up`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio


async def _seed_vendor_and_approved_invoice(
    mk, org_id, *, number: str, amount: Decimal
) -> tuple[str, str]:
    async with mk() as s:
        vendor = Vendor(organization_id=org_id, name="Netting Test Vendor")
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
        await s.refresh(vendor)
        await s.refresh(inv)
        return str(vendor.id), str(inv.id)


async def test_applied_credit_memo_reduces_the_payment_amount(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    vendor_id, invoice_id = await _seed_vendor_and_approved_invoice(
        mk, info.org_id, number="CMNET-001", amount=Decimal("1000.00")
    )

    async with realdb.client(key="a", role="admin") as c:
        memo_resp = await c.post(
            "/api/credit-memos",
            json={
                "memo_number": "CM-NET-1",
                "vendor_id": vendor_id,
                "amount": "300.00",
                "invoice_id": invoice_id,
            },
        )
        assert memo_resp.status_code == 201, memo_resp.text
        assert memo_resp.json()["status"] == "applied"

        run_resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": invoice_id, "method": "ach"}]},
        )
    assert run_resp.status_code == 201, run_resp.text
    body = run_resp.json()
    assert body["payment_count"] == 1
    # $1000.00 invoice - $300.00 applied credit = $700.00 actually paid.
    assert body["total_amount"] == "700.00", body

    async with mk() as s:
        payment = (
            await s.execute(select(Payment).where(Payment.invoice_id == uuid.UUID(invoice_id)))
        ).scalar_one()
        assert payment.amount == Decimal("700.00")


async def test_multiple_applied_credit_memos_stack(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    vendor_id, invoice_id = await _seed_vendor_and_approved_invoice(
        mk, info.org_id, number="CMNET-002", amount=Decimal("1000.00")
    )

    async with realdb.client(key="a", role="admin") as c:
        for i, amount in enumerate(("150.00", "250.00"), start=1):
            memo_resp = await c.post(
                "/api/credit-memos",
                json={
                    "memo_number": f"CM-NET-STACK-{i}",
                    "vendor_id": vendor_id,
                    "amount": amount,
                    "invoice_id": invoice_id,
                },
            )
            assert memo_resp.status_code == 201, memo_resp.text

        run_resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": invoice_id, "method": "ach"}]},
        )
    assert run_resp.status_code == 201, run_resp.text
    # $1000.00 - $150.00 - $250.00 = $600.00.
    assert run_resp.json()["total_amount"] == "600.00"


async def test_invoice_with_no_credit_memo_pays_the_full_amount(realdb):
    """Regression guard against over-netting: an invoice with no applied
    credit must still pay its full amount, unchanged."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    _, invoice_id = await _seed_vendor_and_approved_invoice(
        mk, info.org_id, number="CMNET-003", amount=Decimal("500.00")
    )

    async with realdb.client(key="a", role="admin") as c:
        run_resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": invoice_id, "method": "ach"}]},
        )
    assert run_resp.status_code == 201, run_resp.text
    assert run_resp.json()["total_amount"] == "500.00"
