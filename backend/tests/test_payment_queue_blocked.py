"""`GET /api/payments/queue` must say which rows a payment run would refuse.

`services/payment_runs.create_payment_run_for_invoices` rejects the WHOLE run
with a 409 when ANY selected invoice carries an unresolved
(`open`/`escalated`) exception in `PAYMENT_BLOCKING_EXCEPTION_TYPES`. The queue
offered those rows anyway, so selecting one — with nothing on screen marking it
— produced a hard failure of the entire draft and no indication of which row
caused it.

The queue now resolves `blocked` / `blocked_reason` through the SAME
`payment_runs` helper the run builder uses, so the two can't drift and a new
blocking type updates both surfaces at once. `blocked_reason` is the exception
TYPE only: it is rendered to an operator and travels through a JSON body, and a
description can carry vendor / bank / amount detail.

Requires the dev Postgres (`pnpm db:up`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.models.exception import Exception as ExceptionModel
from app.models.invoice import Invoice, InvoiceStatus

pytestmark = pytest.mark.asyncio

TENANT = "a"

# Description text that must never reach the response: the whole point of
# returning a TYPE rather than the exception row is that a description can
# carry vendor / bank / amount detail.
SENSITIVE_DESCRIPTION = "Bank account 1234567890 for Acme Ltd changed by supplier email"


def _user(uid):
    return SimpleNamespace(id=uid, full_name="Queue Tester", roles=["admin"])


def _org(org_id):
    return SimpleNamespace(id=org_id, name="PyTest", slug="pytesta", settings={})


async def _seed_invoice(
    mk,
    org_id,
    *,
    number: str,
    amount: str = "500.00",
    status: InvoiceStatus = InvoiceStatus.approved,
) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=number,
                vendor_name="Queue Vendor",
                amount=Decimal(amount),
                currency="USD",
                status=status,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
            )
        )
        await s.commit()
    return inv_id


async def _add_exception(
    mk,
    org_id,
    invoice_id: uuid.UUID,
    *,
    exception_type: str,
    status: str = "open",
    description: str | None = None,
) -> None:
    async with mk() as s:
        s.add(
            ExceptionModel(
                id=uuid.uuid4(),
                invoice_id=invoice_id,
                exception_type=exception_type,
                severity="error",
                status=status,
                description=description,
                organization_id=org_id,
            )
        )
        await s.commit()


async def _queue(realdb, mk):
    from app.api.payments import payment_queue

    info = realdb.info(TENANT)
    async with mk() as db:
        result = await payment_queue(
            db=db, org=_org(info.org_id), user=_user(info.users["admin"]), entity_id=None
        )
    return {item["invoice_number"]: item for item in result["items"]}


async def test_unflagged_row_is_not_blocked(realdb):
    mk = realdb.sessionmaker(TENANT)
    await _seed_invoice(mk, realdb.info(TENANT).org_id, number="Q-CLEAN")

    rows = await _queue(realdb, mk)
    assert rows["Q-CLEAN"]["blocked"] is False
    assert rows["Q-CLEAN"]["blocked_reason"] is None


async def test_open_blocking_exception_marks_the_row(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    inv_id = await _seed_invoice(mk, org_id, number="Q-DUP")
    await _add_exception(mk, org_id, inv_id, exception_type="duplicate")

    rows = await _queue(realdb, mk)
    assert rows["Q-DUP"]["blocked"] is True
    assert rows["Q-DUP"]["blocked_reason"] == "duplicate"


async def test_non_blocking_exception_does_not_mark_the_row(realdb):
    """`po_mismatch` is a real exception the queue must keep offering — a run
    does not refuse it, so marking the row would make an payable invoice
    unpayable from the UI."""
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    inv_id = await _seed_invoice(mk, org_id, number="Q-POMM")
    await _add_exception(mk, org_id, inv_id, exception_type="po_mismatch")

    rows = await _queue(realdb, mk)
    assert rows["Q-POMM"]["blocked"] is False
    assert rows["Q-POMM"]["blocked_reason"] is None


async def test_escalated_blocking_exception_still_blocks(realdb):
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    inv_id = await _seed_invoice(mk, org_id, number="Q-ESC")
    await _add_exception(mk, org_id, inv_id, exception_type="fraud_flag", status="escalated")

    rows = await _queue(realdb, mk)
    assert rows["Q-ESC"]["blocked"] is True
    assert rows["Q-ESC"]["blocked_reason"] == "fraud_flag"


@pytest.mark.parametrize("resolution", ["resolved", "dismissed"])
async def test_signed_off_exception_releases_the_row(realdb, resolution):
    """Resolving / dismissing IS the human sign-off — the run builder accepts
    the invoice again, so the queue must too."""
    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    inv_id = await _seed_invoice(mk, org_id, number=f"Q-SIGNED-{resolution}")
    await _add_exception(mk, org_id, inv_id, exception_type="duplicate", status=resolution)

    rows = await _queue(realdb, mk)
    assert rows[f"Q-SIGNED-{resolution}"]["blocked"] is False


async def test_payment_reconciliation_blocks_without_the_queue_restating_the_tuple(realdb):
    """`payment_reconciliation` was added to `PAYMENT_BLOCKING_EXCEPTION_TYPES`
    after the queue was written. It blocks here only because the queue IMPORTS
    the tuple instead of restating it — this test fails the moment someone
    inlines a literal list of types."""
    from app.api.payments import PAYMENT_BLOCKING_EXCEPTION_TYPES

    assert "payment_reconciliation" in PAYMENT_BLOCKING_EXCEPTION_TYPES

    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    inv_id = await _seed_invoice(mk, org_id, number="Q-RECON")
    await _add_exception(mk, org_id, inv_id, exception_type="payment_reconciliation")

    rows = await _queue(realdb, mk)
    assert rows["Q-RECON"]["blocked"] is True
    assert rows["Q-RECON"]["blocked_reason"] == "payment_reconciliation"


async def test_every_blocking_type_is_reported(realdb):
    """Whatever the tuple holds, each member must actually block — so adding a
    type can't leave the queue silently offering rows a run will refuse."""
    from app.api.payments import PAYMENT_BLOCKING_EXCEPTION_TYPES

    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    for exception_type in PAYMENT_BLOCKING_EXCEPTION_TYPES:
        inv_id = await _seed_invoice(mk, org_id, number=f"Q-ALL-{exception_type}")
        await _add_exception(mk, org_id, inv_id, exception_type=exception_type)

    rows = await _queue(realdb, mk)
    for exception_type in PAYMENT_BLOCKING_EXCEPTION_TYPES:
        row = rows[f"Q-ALL-{exception_type}"]
        assert row["blocked"] is True, exception_type
        assert row["blocked_reason"] == exception_type, exception_type


async def test_reason_is_deterministic_when_several_exceptions_are_open(realdb):
    """Two blocking exceptions on one invoice must not make the reason depend
    on row order — the answer is the earliest member of the tuple."""
    from app.api.payments import PAYMENT_BLOCKING_EXCEPTION_TYPES

    mk = realdb.sessionmaker(TENANT)
    org_id = realdb.info(TENANT).org_id
    inv_id = await _seed_invoice(mk, org_id, number="Q-MULTI")
    await _add_exception(mk, org_id, inv_id, exception_type="fraud_flag")
    await _add_exception(mk, org_id, inv_id, exception_type="duplicate")

    first = min(("fraud_flag", "duplicate"), key=PAYMENT_BLOCKING_EXCEPTION_TYPES.index)
    for _ in range(3):
        rows = await _queue(realdb, mk)
        assert rows["Q-MULTI"]["blocked_reason"] == first


async def test_blocked_reason_never_carries_the_exception_description(realdb):
    """PII invariant: the reason is a fixed vocabulary code, never the
    exception's free text (which can name a bank account or a vendor)."""
    import json

    from app.api.payments import payment_queue

    mk = realdb.sessionmaker(TENANT)
    info = realdb.info(TENANT)
    inv_id = await _seed_invoice(mk, info.org_id, number="Q-PII")
    await _add_exception(
        mk,
        info.org_id,
        inv_id,
        exception_type="fraud_flag",
        description=SENSITIVE_DESCRIPTION,
    )

    async with mk() as db:
        result = await payment_queue(
            db=db, org=_org(info.org_id), user=_user(info.users["admin"]), entity_id=None
        )

    payload = json.dumps(result)
    assert "1234567890" not in payload
    assert SENSITIVE_DESCRIPTION not in payload
    row = next(i for i in result["items"] if i["invoice_number"] == "Q-PII")
    assert row["blocked_reason"] == "fraud_flag"


async def test_queue_blocked_set_matches_the_run_builders_own_verdict(realdb):
    """Drift guard: what the queue marks blocked is exactly what
    `payment_runs.blocked_invoice_ids` (the run builder's gate) refuses."""
    from app.api.payments import payment_queue
    from app.services.payment_runs import blocked_invoice_ids

    mk = realdb.sessionmaker(TENANT)
    info = realdb.info(TENANT)
    blocked_id = await _seed_invoice(mk, info.org_id, number="Q-DRIFT-BLOCKED")
    clean_id = await _seed_invoice(mk, info.org_id, number="Q-DRIFT-CLEAN")
    await _add_exception(mk, info.org_id, blocked_id, exception_type="line_total_mismatch")

    async with mk() as db:
        result = await payment_queue(
            db=db, org=_org(info.org_id), user=_user(info.users["admin"]), entity_id=None
        )
        builder_verdict = await blocked_invoice_ids(db, [blocked_id, clean_id])

    queue_verdict = {uuid.UUID(i["id"]) for i in result["items"] if i["blocked"]}
    assert queue_verdict == builder_verdict == {blocked_id}
