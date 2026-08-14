"""A payment run must explain its failures, and let you re-attempt them.

Two defects, one story:

1. **Nothing said WHY.** `Payment.failure_reason` has been populated on every
   failure path since the model was written (compliance refusal, card-issuance
   failure, adapter error, void, webhook failure) but never reached the read
   surface, and the partial-failure counts existed only in the transient
   response body of the `/execute` call that produced them. Reload the run and
   a `partial` was a bare word — the operator's only recourse was the server
   log. `PaymentRunStatus` didn't even name `partial` / `executing` /
   `cancelled`, three of the eight statuses the code actually writes.

2. **There was no way forward.** A failed payment left its invoice occupied by
   a terminal row and the run settled on `partial` for good; re-paying meant
   hand-building a second run. Most of these failures are transient by
   nature — a processor timeout, a rail outage, a compliance hold a human has
   since cleared.

`POST /api/payments/runs/{id}/retry-failed` re-arms ONLY the failed payments
and re-drives them through the same dispatcher `/execute` uses. What it must
never do — and what these tests pin — is re-dispatch a payment that already
succeeded, re-attempt an invoice that is no longer payable, or double-claim an
invoice that has since acquired another live payment.

Runs against the opt-in `realdb` fixture (skips without `pnpm db:up`).
"""

from __future__ import annotations

import contextlib
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun
from app.models.vendor import Vendor
from app.services.payment_adapters.base import PaymentStatus
from app.services.payment_adapters.mock_adapter import MockPaymentAdapter

pytestmark = pytest.mark.asyncio

TENANT = "a"


@contextlib.contextmanager
def _ambient_patches(*extra):
    """Silence sanctions + ERP sync so each test is about the retry leg only."""
    with contextlib.ExitStack() as stack:
        for ctx in (
            patch("app.services.payment_erp_sync.dispatch_payment_sync", new_callable=AsyncMock),
            patch(
                "app.services.compliance.check_payment_compliance",
                new_callable=AsyncMock,
                return_value=SimpleNamespace(verdict="allow", reasons=[]),
            ),
            *extra,
        ):
            stack.enter_context(ctx)
        yield


async def _seed_run(
    mk,
    org_id,
    *,
    run_status: str,
    payments: list[tuple[str, str, str | None]],
    initiated_by: uuid.UUID | None = None,
    invoice_status: InvoiceStatus = InvoiceStatus.approved,
) -> tuple[str, list[str]]:
    """Seed a run whose payments are `(number, payment_status, failure_reason)`.

    Returns `(run_id, [payment_id, ...])` in the order given.
    """
    payment_ids: list[str] = []
    async with mk() as s:
        vendor = Vendor(organization_id=org_id, name="Retry Test Vendor")
        s.add(vendor)
        await s.flush()
        run = PaymentRun(
            organization_id=org_id,
            status=run_status,
            total_amount=Decimal("100.00") * len(payments),
            initiated_by=initiated_by,
            requires_cfo_approval=False,
        )
        s.add(run)
        await s.flush()
        for number, pay_status, reason in payments:
            inv = Invoice(
                organization_id=org_id,
                invoice_number=number,
                vendor_name=vendor.name,
                vendor_id=vendor.id,
                amount=Decimal("100.00"),
                currency="USD",
                status=invoice_status,
            )
            s.add(inv)
            await s.flush()
            payment = Payment(
                invoice_id=inv.id,
                payment_run_id=run.id,
                amount=Decimal("100.00"),
                method="ach",
                status=pay_status,
                failure_reason=reason,
                provider="mock" if pay_status != "pending" else None,
                provider_payment_id=f"px_{number}" if pay_status != "pending" else None,
                correlation_id=uuid.uuid4(),
            )
            s.add(payment)
            await s.flush()
            payment_ids.append(str(payment.id))
        await s.commit()
        return str(run.id), payment_ids


# ---------------------------------------------------------------------------
# 1. The run explains itself
# ---------------------------------------------------------------------------


async def test_run_detail_surfaces_failure_reason_and_rollup(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    run_id, (ok_id, bad_id) = await _seed_run(
        mk,
        org_id,
        run_status="partial",
        payments=[
            ("RETRY-OK-1", "completed", None),
            ("RETRY-BAD-1", "failed", "insufficient_funds"),
        ],
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        r = await c.get(f"/api/payments/runs/{run_id}")
    assert r.status_code == 200, r.text
    body = r.json()

    # The rollup survives a reload — it is derived from the payments, not a
    # transient toast from the /execute response.
    assert body["payment_count"] == 2
    assert body["payments_completed"] == 1
    assert body["payments_failed"] == 1
    assert body["payments_in_flight"] == 0
    assert body["retryable_failures"] == 1

    by_id = {p["id"]: p for p in body["payments"]}
    assert by_id[bad_id]["failure_reason"] == "insufficient_funds"
    assert by_id[ok_id]["failure_reason"] is None
    assert by_id[ok_id]["provider"] == "mock"


async def test_runs_list_surfaces_the_failure_rollup(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    run_id, _ = await _seed_run(
        mk,
        org_id,
        run_status="partial",
        payments=[
            ("RETRY-LIST-OK", "completed", None),
            ("RETRY-LIST-BAD", "failed", "processor_timeout"),
        ],
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        r = await c.get("/api/payments/runs/")
    assert r.status_code == 200, r.text
    item = next(i for i in r.json()["items"] if i["id"] == run_id)
    assert item["payment_count"] == 2
    assert item["payments_completed"] == 1
    assert item["payments_failed"] == 1


async def test_payment_detail_surfaces_failure_reason(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    _, (payment_id,) = await _seed_run(
        mk,
        org_id,
        run_status="failed",
        payments=[("RETRY-DETAIL-1", "failed", "compliance_refusal: sanctions match")],
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        r = await c.get(f"/api/payments/{payment_id}")
    assert r.status_code == 200, r.text
    assert r.json()["failure_reason"] == "compliance_refusal: sanctions match"


async def test_partial_and_cancelled_are_valid_run_statuses():
    """The enum has to name every status the code writes — `/execute` claims a
    run as `executing`, the rollup writes `partial`, `/cancel` writes
    `cancelled`."""
    from app.schemas.payment import PAYMENT_RUN_STATUSES

    for expected in ("draft", "executing", "submitted", "partial", "completed", "cancelled"):
        assert expected in PAYMENT_RUN_STATUSES


# ---------------------------------------------------------------------------
# 2. Retry
# ---------------------------------------------------------------------------


async def test_retry_redispatches_only_the_failed_payment(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    run_id, (ok_id, bad_id) = await _seed_run(
        mk,
        org_id,
        run_status="partial",
        payments=[
            ("RETRY-MIX-OK", "completed", None),
            ("RETRY-MIX-BAD", "failed", "processor_timeout"),
        ],
    )

    async with mk() as s:
        ok_before = await s.get(Payment, uuid.UUID(ok_id))
        ok_correlation_before = ok_before.correlation_id
        bad_correlation_before = (await s.get(Payment, uuid.UUID(bad_id))).correlation_id

    dispatched: list[str] = []

    async def _spy(self, payload):
        dispatched.append(payload.invoice_id)
        return SimpleNamespace(
            success=True,
            status=PaymentStatus.completed,
            provider_payment_id=f"px_retry_{len(dispatched)}",
            reference=f"RETRY-REF-{len(dispatched)}",
            failure_reason=None,
        )

    with _ambient_patches(patch.object(MockPaymentAdapter, "create_payment", _spy)):
        async with realdb.client(key=TENANT, role="admin") as c:
            r = await c.post(f"/api/payments/runs/{run_id}/retry-failed")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["payments_retried"] == 1
    assert body["payments_skipped"] == 0

    # The processor was called exactly once — for the failed payment only.
    assert len(dispatched) == 1, dispatched

    async with mk() as s:
        ok_after = await s.get(Payment, uuid.UUID(ok_id))
        bad_after = await s.get(Payment, uuid.UUID(bad_id))
        # The already-completed payment is untouched: same correlation id, same
        # provider handle, still completed.
        assert ok_after.status == "completed"
        assert ok_after.correlation_id == ok_correlation_before
        assert ok_after.provider_payment_id == "px_RETRY-MIX-OK"
        # The failed one was genuinely re-attempted: new correlation id (the
        # processor's idempotency key), cleared failure, fresh provider handle.
        assert bad_after.status == "completed"
        assert bad_after.correlation_id != bad_correlation_before
        assert bad_after.failure_reason is None
        assert bad_after.provider_payment_id == "px_retry_1"
        assert dispatched == [str(bad_after.invoice_id)]

        run = await s.get(PaymentRun, uuid.UUID(run_id))
        assert run.status == "completed"


async def test_retry_writes_an_audit_row_naming_the_previous_failure(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    run_id, _ = await _seed_run(
        mk,
        org_id,
        run_status="failed",
        payments=[("RETRY-AUDIT-1", "failed", "rail_outage")],
    )

    with _ambient_patches():
        async with realdb.client(key=TENANT, role="admin") as c:
            r = await c.post(f"/api/payments/runs/{run_id}/retry-failed")
    assert r.status_code == 200, r.text

    from app.models.workflow import AuditLog

    async with mk() as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "payment.retried")))
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].details["previous_failure_reason"] == "rail_outage"

        run_rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "payment_run.retried")))
            .scalars()
            .all()
        )
        assert len(run_rows) == 1
        assert run_rows[0].details["payments_retried"] == 1


async def test_retry_is_refused_on_a_run_with_nothing_to_retry(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    draft_id, _ = await _seed_run(
        mk, org_id, run_status="draft", payments=[("RETRY-DRAFT-1", "pending", None)]
    )
    done_id, _ = await _seed_run(
        mk, org_id, run_status="completed", payments=[("RETRY-DONE-1", "completed", None)]
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        draft = await c.post(f"/api/payments/runs/{draft_id}/retry-failed")
        done = await c.post(f"/api/payments/runs/{done_id}/retry-failed")
    assert draft.status_code == 409, draft.text
    assert done.status_code == 409, done.text

    # The draft's payment was never dispatched by the refused retry.
    async with mk() as s:
        run = await s.get(PaymentRun, uuid.UUID(draft_id))
        assert run.status == "draft"


async def test_retry_is_idempotent_across_a_repeat_call(realdb):
    """The second call has nothing failed left to re-arm and 409s against the
    now-`completed` run — it can never produce a second dispatch pass."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    run_id, (payment_id,) = await _seed_run(
        mk, org_id, run_status="failed", payments=[("RETRY-TWICE-1", "failed", "timeout")]
    )

    dispatched: list[str] = []

    async def _spy(self, payload):
        dispatched.append(payload.invoice_id)
        return SimpleNamespace(
            success=True,
            status=PaymentStatus.completed,
            provider_payment_id=f"px_twice_{len(dispatched)}",
            reference="REF",
            failure_reason=None,
        )

    with _ambient_patches(patch.object(MockPaymentAdapter, "create_payment", _spy)):
        async with realdb.client(key=TENANT, role="admin") as c:
            first = await c.post(f"/api/payments/runs/{run_id}/retry-failed")
            second = await c.post(f"/api/payments/runs/{run_id}/retry-failed")

    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text
    assert len(dispatched) == 1, dispatched

    async with mk() as s:
        payment = await s.get(Payment, uuid.UUID(payment_id))
        assert payment.status == "completed"


async def test_retry_skips_an_invoice_that_is_no_longer_payable(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    run_id, (payment_id,) = await _seed_run(
        mk,
        org_id,
        run_status="failed",
        payments=[("RETRY-VOIDED-1", "failed", "timeout")],
        # `rejected` is outside PAYABLE_INVOICE_STATUSES — nobody currently
        # approves paying this.
        invoice_status=InvoiceStatus.rejected,
    )

    dispatched: list[str] = []

    async def _spy(self, payload):
        dispatched.append(payload.invoice_id)
        raise AssertionError("processor must not be called for an unpayable invoice")

    with _ambient_patches(patch.object(MockPaymentAdapter, "create_payment", _spy)):
        async with realdb.client(key=TENANT, role="admin") as c:
            r = await c.post(f"/api/payments/runs/{run_id}/retry-failed")

    assert r.status_code == 200, r.text
    assert r.json()["payments_retried"] == 0
    assert r.json()["skip_reasons"] == ["invoice_not_payable"]
    assert dispatched == []

    async with mk() as s:
        payment = await s.get(Payment, uuid.UUID(payment_id))
        assert payment.status == "failed"
        assert payment.failure_reason == "timeout"


async def test_retry_skips_an_invoice_that_already_has_a_live_payment(realdb):
    """Re-arming would put two live claims on one invoice — exactly what
    `uq_payments_one_live_per_invoice` exists to prevent."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)

    run_id, (payment_id,) = await _seed_run(
        mk, org_id, run_status="failed", payments=[("RETRY-OCCUPIED-1", "failed", "timeout")]
    )

    # Somebody re-booked the invoice standalone after the run failed.
    async with mk() as s:
        failed = await s.get(Payment, uuid.UUID(payment_id))
        s.add(
            Payment(
                invoice_id=failed.invoice_id,
                amount=Decimal("100.00"),
                method="ach",
                status="pending",
                correlation_id=uuid.uuid4(),
            )
        )
        await s.commit()

    with _ambient_patches():
        async with realdb.client(key=TENANT, role="admin") as c:
            r = await c.post(f"/api/payments/runs/{run_id}/retry-failed")

    assert r.status_code == 200, r.text
    assert r.json()["payments_retried"] == 0
    assert r.json()["skip_reasons"] == ["invoice_has_live_payment"]

    async with mk() as s:
        payment = await s.get(Payment, uuid.UUID(payment_id))
        assert payment.status == "failed"


async def test_retry_honours_maker_checker_segregation(realdb):
    """Re-attempting moves money exactly like /execute, so the run's own
    creator can't drive it solo."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)

    run_id, _ = await _seed_run(
        mk,
        info.org_id,
        run_status="failed",
        payments=[("RETRY-SOD-1", "failed", "timeout")],
        initiated_by=info.users["admin"],
    )

    with _ambient_patches():
        async with realdb.client(key=TENANT, role="admin") as c:
            r = await c.post(f"/api/payments/runs/{run_id}/retry-failed")
    assert r.status_code == 403, r.text
