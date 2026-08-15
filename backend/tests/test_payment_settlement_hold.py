"""An under-settled invoice must not read as settled in full — and must not strand.

`verify_settlement` could already tell that a rail moved a different figure
than AP authorized. What it could not do was stop the consequence: the payment
stayed legitimately `completed`, `payment_erp_sync` flipped the invoice to
`paid`, and the ERP, the aging report and the 1099 YTD totals all recorded a
$500 payable as settled while the vendor had received $250.

Closing that needs BOTH halves, and shipping only the first is worse than
shipping neither. An earlier attempt held the invoice with no way to release
it: `payment_erp_sync._sync_payments` is the only code that flips
`payment_scheduled → paid`, nothing re-invokes it once a run's payments are
terminal, and the hold was keyed on a resolvable exception — so an operator who
cleared the flag (the correct response to an OVER-settlement) stranded the
invoice permanently, never paid and never re-payable. It was reverted.

These tests pin both halves together:

* the hold itself, keyed on the figure PERSISTED on the payment row so it is a
  durable fact rather than the transient state of a flag someone clears;
* the three ways out — accept the shortfall as final, void and re-pay, or
  simply be covered — and the fail-open cases (an amount-free rail, an
  over-settlement) that must never hold at all.

`test_void_is_an_exit_from_the_hold` is the direct regression guard for the
reverted defect: if a held invoice can't reach a terminal state by any route,
that test fails.

DB-backed via `realdb`, reusing the `_sync_payments` harness conventions from
`test_payment_erp_sync.py` (which redirects `settings.database_url` at this
slot's own control-plane DB — see that file's fixture docstring).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.payment import Payment, PaymentRun
from app.models.workflow import AuditLog
from app.services.payment_erp_sync import _sync_payments

TENANT = "a"


@pytest.fixture(autouse=True)
def _redirect_database_url_to_slot(realdb, monkeypatch):
    """Same redirect `test_payment_erp_sync.py` documents at length.

    `_sync_payments` builds its own control engine straight off
    `settings.database_url` rather than the shared factory, so it cannot pick
    up the per-slot control-plane DB unless the global is redirected.
    """
    monkeypatch.setattr(settings, "database_url", realdb.control_db_url())


async def _set_org_erp(realdb, key: str, erp_config: dict | None) -> None:
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        org = (
            await s.execute(select(Organization).where(Organization.id == realdb.info(key).org_id))
        ).scalar_one()
        new_settings = dict(org.settings or {})
        if erp_config is None:
            new_settings.pop("erp", None)
        else:
            new_settings["erp"] = erp_config
        org.settings = new_settings
        await s.commit()


async def _seed_run(
    realdb,
    key: str,
    *,
    amount: Decimal = Decimal("500.00"),
    currency: str = "USD",
    settled_amount: Decimal | None = None,
    settled_currency: str | None = None,
    payment_status: str = "completed",
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Create a run + `payment_scheduled` invoice + payment.

    Returns (run_id, invoice_id, payment_id).
    """
    mk = realdb.sessionmaker(key)
    org_id = realdb.info(key).org_id
    run_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    async with mk() as s:
        ent = (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()
        s.add(PaymentRun(id=run_id, status="completed", organization_id=org_id, entity_id=ent))
        s.add(
            Invoice(
                id=invoice_id,
                entity_id=ent,
                invoice_number=f"INV-HOLD-{uuid.uuid4().hex[:8]}",
                vendor_name="Vendor Co",
                amount=amount,
                currency=currency,
                status=InvoiceStatus.payment_scheduled,
                organization_id=org_id,
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=payment_id,
                entity_id=ent,
                invoice_id=invoice_id,
                payment_run_id=run_id,
                amount=amount,
                method="ach",
                status=payment_status,
                settled_amount=settled_amount,
                settled_currency=settled_currency,
            )
        )
        await s.commit()
    return run_id, invoice_id, payment_id


async def _invoice_status(realdb, key: str, invoice_id) -> InvoiceStatus:
    mk = realdb.sessionmaker(key)
    async with mk() as s:
        return (
            (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one().status
        )


# ---------------------------------------------------------------------------
# The hold
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_under_settled_payment_holds_the_invoice(realdb):
    """$250 against a $500 instruction must not close the payable out."""
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id, _ = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
    )

    await _sync_payments(run_id, realdb.info(TENANT).org_id)

    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.payment_scheduled


@pytest.mark.asyncio
async def test_settlement_in_an_unauthorized_currency_holds_the_invoice(realdb):
    """Money on a currency we never authorized can't be called a settlement."""
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id, _ = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        currency="USD",
        settled_amount=Decimal("500.00"),
        settled_currency="JPY",
    )

    await _sync_payments(run_id, realdb.info(TENANT).org_id)

    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.payment_scheduled


@pytest.mark.asyncio
async def test_over_settled_payment_still_marks_paid(realdb):
    """Over-settlement is flagged by the verifier but the vendor is not short —
    holding here would strand an invoice for no protective reason."""
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id, _ = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("750.00"),
        settled_currency="USD",
    )

    await _sync_payments(run_id, realdb.info(TENANT).org_id)

    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.paid


@pytest.mark.asyncio
async def test_amount_free_rail_still_marks_paid(realdb):
    """The Dwolla case, at the sync layer.

    A rail whose webhook carries no amount leaves `settled_amount` NULL. If
    NULL held, every payment on that rail — and every row predating migration
    0083 — would strand.
    """
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id, _ = await _seed_run(
        realdb, TENANT, amount=Decimal("500.00"), settled_amount=None
    )

    await _sync_payments(run_id, realdb.info(TENANT).org_id)

    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.paid


# ---------------------------------------------------------------------------
# The exits — this is what the reverted attempt lacked
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_releases_a_held_invoice_to_paid(realdb):
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
    )
    await _sync_payments(run_id, realdb.info(TENANT).org_id)
    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.payment_scheduled

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "Vendor agreed to the short payment; balance credited next cycle."},
        )

    assert resp.status_code == 200, resp.text
    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.paid


@pytest.mark.asyncio
async def test_accept_records_the_shortfall_on_the_immutable_trail(realdb):
    """Accepting a shortfall is a financial decision — it needs a durable record
    with the figures, as exact strings and free of PII."""
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    _, _, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "Short-paid by agreement."},
        )
    assert resp.status_code == 200, resp.text

    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        row = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "payment.settlement_accepted",
                    AuditLog.entity_id == payment_id,
                )
            )
        ).scalar_one()
    assert row.details["coverage"] == "short"
    assert row.details["shortfall"] == "250.00"
    assert row.details["authorized_amount"] == "500.00"
    assert row.details["settled_amount"] == "250.00"


@pytest.mark.asyncio
async def test_void_is_an_exit_from_the_hold(realdb):
    """The direct regression guard for the reverted defect.

    A held invoice must be reachable out of `payment_scheduled` by the ordinary
    void path too — that is what makes the hold safe rather than a strand.
    Voiding returns it to `approved` so it can be re-paid correctly, which is
    the right remedy when the settlement itself was wrong.
    """
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
    )
    await _sync_payments(run_id, realdb.info(TENANT).org_id)
    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.payment_scheduled

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/payments/{payment_id}/void",
            json={"reason": "Processor settled short; re-paying in full."},
        )

    assert resp.status_code == 200, resp.text
    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.approved


# ---------------------------------------------------------------------------
# Accept is not a general-purpose "force to paid" lever
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_refuses_when_the_settlement_already_covers(realdb):
    _, _, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("500.00"),
        settled_currency="USD",
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "nothing wrong here"},
        )

    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_accept_refuses_a_payment_that_never_completed(realdb):
    """You can only accept a settlement that actually happened."""
    _, _, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
        payment_status="submitted",
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "too early"},
        )

    assert resp.status_code == 409, resp.text


@pytest.mark.asyncio
async def test_accept_requires_the_money_moving_permission(realdb):
    """Closing a payable out on a short settlement is the same authority as
    executing or voiding — a clerk must not hold it."""
    _, _, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
    )

    async with realdb.client(key=TENANT, role="ap_clerk") as client:
        resp = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "let me through"},
        )

    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_accept_leaves_the_fraud_flag_for_the_exception_queue(realdb):
    """`fraud_flag` is shared with Positive Pay's altered-cheque detection, so
    auto-resolving "the open one" here could silently close an unrelated fraud
    finding. The queue stays the separate human sign-off."""
    from app.models.exception import Exception as APException

    _, invoice_id, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
    )
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        s.add(
            APException(
                invoice_id=invoice_id,
                exception_type="fraud_flag",
                severity="error",
                status="open",
                description="Settlement amount mismatch",
                organization_id=realdb.info(TENANT).org_id,
            )
        )
        await s.commit()

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "Short-paid by agreement."},
        )
    assert resp.status_code == 200, resp.text

    async with mk() as s:
        still_open = (
            await s.execute(
                select(func.count())
                .select_from(APException)
                .where(
                    APException.invoice_id == invoice_id,
                    APException.exception_type == "fraud_flag",
                    APException.status == "open",
                )
            )
        ).scalar_one()
    assert still_open == 1


@pytest.mark.asyncio
async def test_accept_refuses_a_second_time(realdb):
    """The first call's audit row is the record. A retry must not let an
    operator re-justify the same acceptance on the immutable trail."""
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    run_id, invoice_id, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
    )
    await _sync_payments(run_id, realdb.info(TENANT).org_id)

    async with realdb.client(key=TENANT, role="admin") as client:
        first = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "Vendor agreed."},
        )
        second = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "changed my mind about why"},
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert await _invoice_status(realdb, TENANT, invoice_id) == InvoiceStatus.paid

    # Exactly one acceptance on the trail, carrying the FIRST reason.
    mk = realdb.sessionmaker(TENANT)
    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "payment.settlement_accepted",
                        AuditLog.entity_id == payment_id,
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].details["reason"] == "Vendor agreed."


@pytest.mark.asyncio
async def test_accept_response_shows_what_was_settled(realdb):
    """Without the settled figures on the read surface, an operator seeing a
    `completed` payment whose invoice is held has no way to tell why."""
    await _set_org_erp(realdb, TENANT, {"type": "mock", "integration_method": "direct"})
    run_id, _, payment_id = await _seed_run(
        realdb,
        TENANT,
        amount=Decimal("500.00"),
        settled_amount=Decimal("250.00"),
        settled_currency="USD",
    )
    await _sync_payments(run_id, realdb.info(TENANT).org_id)

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/payments/{payment_id}/settlement/accept",
            json={"reason": "Vendor agreed."},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # `OptionalMoneyAmount` — the same JSON-number wire encoding every other
    # money field on this schema uses (`schemas/money.py` documents why, and
    # `PaymentRunResponse.total_amount` is the precedent). The in-Python value
    # stays `Decimal`; only the JSON hop converts.
    assert body["settled_amount"] == 250.00
    assert body["settled_currency"] == "USD"
    assert body["amount"] == 500.00


@pytest.mark.asyncio
async def test_unreported_settlement_serializes_as_null_not_zero(realdb):
    """`None` on the read surface means "no rail reported a figure" — a 0 here
    would read as a total shortfall."""
    _, _, payment_id = await _seed_run(realdb, TENANT, amount=Decimal("500.00"))

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.get(f"/api/payments/{payment_id}")

    assert resp.status_code == 200, resp.text
    assert resp.json()["settled_amount"] is None
