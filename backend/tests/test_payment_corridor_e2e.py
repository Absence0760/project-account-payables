"""End-to-end regression for issue #123 — the real `create_payment_run`
default must not be trusted as an explicit corridor override.

The unit-level `execute_payment_run` tests in `test_international_payments.py`
hand-construct a `Payment(method=None)` object directly, which bypasses the
bug entirely: `create_payment_run` (and the frontend) always default a line
item's method to `"ach"`. This test drives the REAL HTTP endpoints — create
then execute — so the payment row goes through the actual default, proving
a cross-currency payment is routed onto `international_wire` (with the FX
leg locked) rather than shipped out as `ach` + a foreign currency.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor

TENANT = "a"
_VALID_DE_IBAN = "DE89370400440532013000"


@pytest.mark.asyncio
async def test_create_payment_run_default_method_still_routes_cross_currency_correctly(realdb):
    """A EUR invoice on a USD-home org, submitted to `POST /payments/runs`
    with NO `method` specified (so Pydantic's own `"ach"` default applies,
    exactly like the frontend), must settle on `international_wire` with the
    FX leg locked — not go out as a domestic `ach` payment in a foreign
    currency."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    vendor_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                name="Berlin Supplies GmbH",
                organization_id=org_id,
                bank_details={
                    "iban": _VALID_DE_IBAN,
                    "iban_last4": "3000",
                    "swift_bic": "DEUTDEFF",
                    "country": "DE",
                },
            )
        )
        s.add(
            Invoice(
                id=invoice_id,
                invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
                vendor_name="Berlin Supplies GmbH",
                vendor_id=vendor_id,
                amount=Decimal("2000.00"),
                currency="EUR",
                status=InvoiceStatus.approved,
                organization_id=org_id,
                invoice_date=date.today(),
                correlation_id=uuid.uuid4(),
            )
        )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as client:
        create_resp = await client.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": str(invoice_id)}]},  # method omitted -> "ach" default
        )
        assert create_resp.status_code == 201, create_resp.text
        run_id = create_resp.json()["id"]

    async with mk() as s:
        pre_execute = (
            await s.execute(select(Payment).where(Payment.invoice_id == invoice_id))
        ).scalar_one()
        # The create step really did default to "ach" — this test would be
        # meaningless if it didn't reproduce the real default.
        assert pre_execute.method == "ach"

    async with realdb.client(key=TENANT, role="ap_manager") as client:
        exec_resp = await client.post(f"/api/payments/runs/{run_id}/execute")
    assert exec_resp.status_code == 200, exec_resp.text

    async with mk() as s:
        payment = (
            await s.execute(select(Payment).where(Payment.invoice_id == invoice_id))
        ).scalar_one()
        assert payment.method == "international_wire", (
            "a defaulted 'ach' method must not survive execution for a "
            "cross-currency payment — auto-selection must have overridden it"
        )
        assert payment.fx_rate is not None, "the FX leg must be locked for a cross-currency payment"
        # source_currency is the org's home currency the payment is funded
        # from (USD); the invoice's own EUR is the target side of the corridor.
        assert payment.source_currency == "USD"
