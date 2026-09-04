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
from sqlalchemy import select

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


# ---------------------------------------------------------------------------
# `total_rebates` is denominated, and scoped, like every other figure here
# ---------------------------------------------------------------------------


async def _seed_card_rebate(
    mk,
    org_id,
    *,
    card_currency: str,
    rebate_amount: str,
    entity_id=None,
) -> uuid.UUID:
    """One `VirtualCard` + its `CardRebate`, written directly.

    `CardRebate` carries no currency column, so the card's currency is the
    only place a rebate's denomination lives — which is why the rollup has to
    join, and why this seeds the pair rather than a bare rebate row.
    """
    from app.models.virtual_card import CardRebate, VirtualCard

    inv_id = await _seed_invoice_with_payment(
        mk,
        org_id,
        invoice_currency=card_currency,
        amount=rebate_amount,
        status="completed",
    )
    async with mk() as s:
        card = VirtualCard(
            id=uuid.uuid4(),
            invoice_id=inv_id,
            organization_id=org_id,
            card_provider="mock",
            provider_card_id=f"card_{uuid.uuid4().hex[:10]}",
            amount_limit=Decimal(rebate_amount),
            status="active",
            currency=card_currency,
        )
        if entity_id is not None:
            card.entity_id = entity_id
        s.add(card)
        await s.flush()
        s.add(
            CardRebate(
                virtual_card_id=card.id,
                organization_id=org_id,
                amount=Decimal(rebate_amount),
                rate=Decimal("0.0100"),
                status="confirmed",
                period=datetime.now(UTC).strftime("%Y-%m"),
            )
        )
        await s.commit()
        return card.id


async def _summary(realdb, org_id, *, reporting_currency="USD", entity_id=None):
    from app.api.payments import payment_summary

    async with realdb.sessionmaker(TENANT)() as s:
        return await payment_summary(
            db=s,
            org=_org(org_id, reporting_currency=reporting_currency),
            user=_user(uuid.uuid4()),
            entity_id=entity_id,
        )


async def test_total_rebates_reports_one_currency_and_discloses_the_rest(realdb):
    """It was a bare cross-currency `SUM(CardRebate.amount)` shipped under the
    `currency` this same response declares — a quantity in no currency at all.

    A rebate's currency is only knowable through its card, so this is the same
    `VirtualCard` join `GET /api/cards/dashboard` needs for the same reason.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_card_rebate(mk, org_id, card_currency="USD", rebate_amount="10.00")
    await _seed_card_rebate(mk, org_id, card_currency="EUR", rebate_amount="7.00")
    await _seed_card_rebate(mk, org_id, card_currency="GBP", rebate_amount="5.00")

    res = await _summary(realdb, org_id)

    # 10.00 under USD — never 22.00, which is the pre-fix figure.
    assert Decimal(res["total_rebates"]) == Decimal("10.00")
    assert res["currency"] == "USD"
    assert res["excluded_rebate_count"] == 2


async def test_the_rebate_exclusion_count_is_separate_from_the_payment_one(realdb):
    """Two different claims, tracked independently.

    `unconverted_payment_count` is a payment whose reporting-currency figure
    could not be ESTABLISHED; `excluded_rebate_count` is a rebate whose
    currency is known and simply is not this one. Folding them into one number
    would describe neither — so an unconvertible PAYMENT must not inflate the
    rebate count, which is the direction that would mislead (the rebate figure
    would look partial when it is complete).
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    # An unconvertible payment with no card behind it at all.
    await _seed_invoice_with_payment(
        mk, org_id, invoice_currency="EUR", amount="60.00", status="completed"
    )
    # A rebate that IS in the reporting currency.
    await _seed_card_rebate(mk, org_id, card_currency="USD", rebate_amount="4.00")

    res = await _summary(realdb, org_id)
    assert res["unconverted_payment_count"] == 1, "the EUR payment is unconvertible"
    assert res["excluded_rebate_count"] == 0, (
        "the only rebate is in the reporting currency — a payment exclusion "
        "must not make the rebate figure look partial"
    )
    assert Decimal(res["total_rebates"]) == Decimal("4.00")


async def test_a_lowercase_card_currency_still_counts(realdb):
    """`usd` is USD.

    `resolve_reporting_currency` always returns an uppercase code, so an
    un-normalised comparison would exclude every row rather than fail loudly —
    which is why `card_currency_sql` uppercases.

    It does NOT also assert the `COALESCE` half. `virtual_cards.currency` is
    `nullable=False` with a Python-side `default="USD"`, so a card seeded with
    `currency=None` persists `'USD'` and the coalesce branch is never reached —
    a test of it here would pass with the coalesce deleted. The coalesce stays
    for parity with `api/cards.py`'s identical expression, but it is not
    load-bearing and is not claimed to be.
    """
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    await _seed_card_rebate(mk, org_id, card_currency="usd", rebate_amount="3.00")
    await _seed_card_rebate(mk, org_id, card_currency="USD", rebate_amount="2.00")

    res = await _summary(realdb, org_id)
    assert Decimal(res["total_rebates"]) == Decimal("5.00")
    assert res["excluded_rebate_count"] == 0


async def test_total_rebates_is_scoped_to_the_selected_entity(realdb):
    """The rest of this response is entity-scoped, so a summary mixing an
    entity-scoped outflow with an org-wide rebate can't be reconciled against
    either. The comment this replaced claimed to match an org-wide dashboard
    KPI; that dashboard is entity-scoped now, so the claim had inverted."""
    from app.models.entity import Entity

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        other = Entity(organization_id=org_id, name="Sub B", slug=f"sub-{uuid.uuid4().hex[:6]}")
        s.add(other)
        await s.commit()
        other_id = other.id
        default_id = (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()

    await _seed_card_rebate(
        mk, org_id, card_currency="USD", rebate_amount="9.00", entity_id=other_id
    )
    await _seed_card_rebate(
        mk, org_id, card_currency="USD", rebate_amount="1.00", entity_id=default_id
    )

    scoped = await _summary(realdb, org_id, entity_id=default_id)
    assert Decimal(scoped["total_rebates"]) == Decimal("1.00")

    # No entity selected is the consolidated read — every entity included.
    consolidated = await _summary(realdb, org_id, entity_id=None)
    assert Decimal(consolidated["total_rebates"]) == Decimal("10.00")
