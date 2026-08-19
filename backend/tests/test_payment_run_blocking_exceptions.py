"""`POST /api/payments/runs` — which exception TYPES block a run, over a real DB.

`test_payment_run_critical_path.py` covers this gate with a mocked session, so
it stubs the blocking query's *result* and therefore proves nothing about which
`exception_type` values actually match. These tests insert real `Exception` rows
and drive the real query, so the membership of
`payments.PAYMENT_BLOCKING_EXCEPTION_TYPES` is pinned by behaviour.

That membership matters because **approval does not gate on any of it**: nothing
in `services/review.py` or `workflow_engine.py` reads warning severity, so an
`error`-severity flag can be approved straight past. Payment-run creation is the
gate that stops the money.

`line_total_mismatch` is the case that prompted these: an invoice whose header
`amount` openly disagrees with its own line items must not be pulled into a run,
because the run pays the header. The header is deliberately never recomputed
from the lines (see `docs/line-total-reconciliation.md`), so a human has to
reconcile the two and clear the exception first.

Runs against the opt-in `realdb` fixture (skips without `pnpm db:up`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun

pytestmark = pytest.mark.asyncio


async def _seed_approved_invoice(mk, org_id, *, number: str) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                invoice_number=number,
                vendor_name="Blocking Gate Vendor",
                amount=Decimal("100.00"),
                currency="USD",
                status=InvoiceStatus.approved,
            )
        )
        await s.commit()
    return inv_id


async def _add_exception(mk, org_id, invoice_id, *, exc_type: str, status: str = "open") -> None:
    async with mk() as s:
        s.add(
            APException(
                id=uuid.uuid4(),
                organization_id=org_id,
                invoice_id=invoice_id,
                exception_type=exc_type,
                severity="error",
                description="seeded by test",
                status=status,
            )
        )
        await s.commit()


async def _run_count(mk) -> int:
    async with mk() as s:
        return (await s.execute(select(func.count(PaymentRun.id)))).scalar_one()


async def _payment_count(mk) -> int:
    async with mk() as s:
        return (await s.execute(select(func.count(Payment.id)))).scalar_one()


@pytest.mark.parametrize(
    "exc_type",
    # Every member of PAYMENT_BLOCKING_EXCEPTION_TYPES, `payment_reconciliation`
    # included: it was added to the tuple without being added here, so the
    # newest blocking type was the one member nothing proved actually blocks.
    ["line_total_mismatch", "duplicate", "fraud_flag", "payment_reconciliation"],
)
async def test_unresolved_blocking_exception_refuses_the_run(realdb, exc_type):
    """An approved invoice carrying an unresolved financial-integrity exception
    cannot enter a payment run — 409, and no run or payment row is created.

    The refusal must also name the type that ACTUALLY blocked it. The message
    used to recite a fixed "duplicate/fraud/line-total" list, so a
    `payment_reconciliation` hold was refused with three causes it doesn't
    carry — sending the operator to clear an exception that isn't there."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_approved_invoice(mk, info.org_id, number=f"PRB-{exc_type}-1")
    await _add_exception(mk, info.org_id, inv_id, exc_type=exc_type)

    runs_before, payments_before = await _run_count(mk), await _payment_count(mk)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": str(inv_id), "method": "ach"}]},
        )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert f"PRB-{exc_type}-1" in detail
    # The real reason, not a hardcoded list of causes.
    assert exc_type in detail, detail
    # Nothing was booked.
    assert await _run_count(mk) == runs_before
    assert await _payment_count(mk) == payments_before


@pytest.mark.parametrize("cleared_status", ["resolved", "dismissed"])
async def test_cleared_line_total_mismatch_lets_the_run_proceed(realdb, cleared_status):
    """Resolving or dismissing the exception IS the human sign-off — the gate
    keys on `open`/`escalated` only, so a cleared flag must not strand the
    invoice. This is the documented escape hatch, not a bypass."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_approved_invoice(mk, info.org_id, number=f"PRB-CLEARED-{cleared_status}")
    await _add_exception(
        mk, info.org_id, inv_id, exc_type="line_total_mismatch", status=cleared_status
    )

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": str(inv_id), "method": "ach"}]},
        )
    assert resp.status_code == 201, resp.text
    assert resp.json()["payment_count"] == 1


async def test_escalated_line_total_mismatch_still_blocks(realdb):
    """`escalated` is an *unresolved* state — it means a human is still on it."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_approved_invoice(mk, info.org_id, number="PRB-ESCALATED-1")
    await _add_exception(
        mk, info.org_id, inv_id, exc_type="line_total_mismatch", status="escalated"
    )

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": str(inv_id), "method": "ach"}]},
        )
    assert resp.status_code == 409, resp.text


async def test_a_clean_invoice_in_the_same_batch_is_not_collateral_damage(realdb):
    """The gate refuses the whole run, naming only the offending invoice — the
    operator has to drop or clear it, not guess. Nothing is partially booked."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    bad = await _seed_approved_invoice(mk, info.org_id, number="PRB-BATCH-BAD")
    good = await _seed_approved_invoice(mk, info.org_id, number="PRB-BATCH-GOOD")
    await _add_exception(mk, info.org_id, bad, exc_type="line_total_mismatch")

    runs_before = await _run_count(mk)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={
                "items": [
                    {"invoice_id": str(bad), "method": "ach"},
                    {"invoice_id": str(good), "method": "ach"},
                ]
            },
        )
    assert resp.status_code == 409, resp.text
    assert "PRB-BATCH-BAD" in resp.json()["detail"]
    assert "PRB-BATCH-GOOD" not in resp.json()["detail"]
    assert await _run_count(mk) == runs_before

    # The clean invoice on its own still pays.
    async with realdb.client(key="a", role="admin") as c:
        ok = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": str(good), "method": "ach"}]},
        )
    assert ok.status_code == 201, ok.text


async def test_a_non_blocking_exception_type_does_not_block(realdb):
    """Only the financial-integrity classes gate payment. A `po_mismatch` is
    real but advisory here — widening the tuple silently would strand ordinary
    invoices, so the membership is pinned in both directions."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_approved_invoice(mk, info.org_id, number="PRB-PO-1")
    await _add_exception(mk, info.org_id, inv_id, exc_type="po_mismatch")

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/payments/runs",
            json={"items": [{"invoice_id": str(inv_id), "method": "ach"}]},
        )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# The standalone money path runs the SAME gate.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("exc_type", ["line_total_mismatch", "duplicate", "fraud_flag"])
async def test_standalone_payment_refuses_a_blocked_invoice(realdb, exc_type):
    """`POST /api/payments` books money exactly like executing a run, so it has
    to re-check the same financial-integrity flags.

    It didn't: `blocked_invoice_ids` had two call sites (run creation and
    `/retry-failed`) and this one was not among them, so an invoice the run path
    refuses with a 409 could be paid by posting it here instead — a complete
    bypass of the gate for anyone holding `payment.execute`.
    """
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_approved_invoice(mk, info.org_id, number=f"PRB-SOLO-{exc_type}")
    await _add_exception(mk, info.org_id, inv_id, exc_type=exc_type)

    payments_before = await _payment_count(mk)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/payments", json={"invoice_id": str(inv_id), "method": "ach"})
    assert resp.status_code == 409, resp.text
    assert f"PRB-SOLO-{exc_type}" in resp.json()["detail"]
    assert await _payment_count(mk) == payments_before


@pytest.mark.parametrize("cleared_status", ["resolved", "dismissed"])
async def test_standalone_payment_proceeds_once_the_flag_is_cleared(realdb, cleared_status):
    """Same escape hatch as the run path — clearing the exception IS the human
    sign-off, and must not strand the invoice on this route either."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_approved_invoice(
        mk, info.org_id, number=f"PRB-SOLO-CLEARED-{cleared_status}"
    )
    await _add_exception(mk, info.org_id, inv_id, exc_type="fraud_flag", status=cleared_status)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/payments", json={"invoice_id": str(inv_id), "method": "ach"})
    assert resp.status_code == 201, resp.text
