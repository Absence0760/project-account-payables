"""`/payments/summary` and `/payments/queue` must not sum across currencies.

`Payment.amount` is denominated in the INVOICE's currency —
`international_payments.prepare_international_payment` sets
`amount=invoice.amount` and puts the home-currency debit on `source_amount` —
so both KPI endpoints were adding a EUR payment into a USD total at face value,
with nothing in the response saying which currency the number was in. The same
defect the 1099 report carried until round 10, and the same one
`services/compliance.py` and `docs/multi-currency.md` already call out
elsewhere.

Also pinned here: `total_pending` used to omit `pending_compliance`, so money
held by the sanctions gate appeared in NEITHER KPI — not paid, not pending.

Requires the dev Postgres (`pnpm db:up`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio

TENANT = "a"


def _user(uid):
    return SimpleNamespace(id=uid, full_name="Currency Tester", roles=["admin"])


def _org(org_id, *, reporting_currency: str = "USD"):
    return SimpleNamespace(
        id=org_id,
        name="PyTest",
        slug="pytesta",
        settings={"reporting_currency": reporting_currency},
    )


async def _seed_invoice_with_payment(
    mk,
    org_id,
    *,
    invoice_currency: str,
    amount: str,
    status: str,
    source_amount: str | None = None,
    source_currency: str | None = None,
    invoice_status: InvoiceStatus = InvoiceStatus.payment_scheduled,
) -> uuid.UUID:
    inv_id = uuid.uuid4()
    vendor_id = uuid.uuid4()
    async with mk() as s:
        s.add(Vendor(id=vendor_id, name=f"V-{uuid.uuid4().hex[:6]}", organization_id=org_id))
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=f"CUR-{uuid.uuid4().hex[:8]}",
                vendor_name="V",
                vendor_id=vendor_id,
                amount=Decimal(amount),
                currency=invoice_currency,
                status=invoice_status,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=uuid.uuid4(),
                invoice_id=inv_id,
                amount=Decimal(amount),
                method="ach",
                status=status,
                correlation_id=uuid.uuid4(),
                completed_at=datetime.now(UTC) if status == "completed" else None,
                source_amount=Decimal(source_amount) if source_amount else None,
                source_currency=source_currency,
            )
        )
        await s.commit()
    return inv_id


async def test_summary_excludes_an_unconvertible_payment_and_says_so(realdb):
    from app.api.payments import payment_summary

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)

    await _seed_invoice_with_payment(
        mk, info.org_id, invoice_currency="USD", amount="100.00", status="completed"
    )
    # A EUR invoice paid with no home-currency leg: neither rung can establish
    # a USD figure, so it must be excluded and counted — never added as 250 USD.
    await _seed_invoice_with_payment(
        mk, info.org_id, invoice_currency="EUR", amount="250.00", status="completed"
    )

    async with mk() as db:
        result = await payment_summary(
            db=db, org=_org(info.org_id), user=_user(info.users["admin"]), entity_id=None
        )

    assert result["currency"] == "USD"
    assert Decimal(result["total_paid"]) == Decimal("100.00")
    assert result["unconverted_payment_count"] == 1


async def test_summary_uses_the_locked_home_currency_leg(realdb):
    """An FX payment carrying `source_amount` in the reporting currency
    contributes THAT figure — the money that actually left the bank — not the
    invoice-currency face value."""
    from app.api.payments import payment_summary

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)

    await _seed_invoice_with_payment(
        mk,
        info.org_id,
        invoice_currency="EUR",
        amount="250.00",
        status="completed",
        source_amount="275.50",
        source_currency="USD",
    )

    async with mk() as db:
        result = await payment_summary(
            db=db, org=_org(info.org_id), user=_user(info.users["admin"]), entity_id=None
        )

    assert Decimal(result["total_paid"]) == Decimal("275.50")
    assert result["unconverted_payment_count"] == 0


async def test_pending_total_includes_compliance_holds(realdb):
    """A payment held by the sanctions gate is authorized money still out
    there. Omitting `pending_compliance` put it in neither KPI."""
    from app.api.payments import payment_summary

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)

    await _seed_invoice_with_payment(
        mk,
        info.org_id,
        invoice_currency="USD",
        amount="900.00",
        status="pending_compliance",
        invoice_status=InvoiceStatus.approved,
    )

    async with mk() as db:
        result = await payment_summary(
            db=db, org=_org(info.org_id), user=_user(info.users["admin"]), entity_id=None
        )

    assert Decimal(result["total_pending"]) == Decimal("900.00")


async def test_queue_totals_are_reporting_currency_and_flag_the_rest(realdb):
    from app.api.payments import payment_queue

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)

    # Two payable invoices with no payment yet — one domestic, one foreign with
    # no locked reporting figure.
    for currency, amount in (("USD", "100.00"), ("EUR", "250.00")):
        inv_id = uuid.uuid4()
        vendor_id = uuid.uuid4()
        async with mk() as s:
            s.add(
                Vendor(id=vendor_id, name=f"Q-{uuid.uuid4().hex[:6]}", organization_id=info.org_id)
            )
            s.add(
                Invoice(
                    id=inv_id,
                    invoice_number=f"QCUR-{uuid.uuid4().hex[:8]}",
                    vendor_name="Q",
                    vendor_id=vendor_id,
                    amount=Decimal(amount),
                    currency=currency,
                    status=InvoiceStatus.approved,
                    organization_id=info.org_id,
                    correlation_id=uuid.uuid4(),
                )
            )
            await s.commit()

    async with mk() as db:
        result = await payment_queue(
            db=db, org=_org(info.org_id), user=_user(info.users["admin"]), entity_id=None
        )

    assert result["currency"] == "USD"
    # The foreign row is still counted (dropping it would understate what's
    # due) but the response says one row entered at face value.
    assert result["unconverted_count"] == 1
    # Each row keeps its own currency for display.
    currencies = {i["currency"] for i in result["items"]}
    assert {"USD", "EUR"} <= currencies
