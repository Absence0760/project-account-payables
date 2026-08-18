"""POST /api/invoices/bulk/status must not bypass the approval controls.

Routing a bulk transition straight to `approved` skipped segregation-of-duties,
the max-amount cap, and the CFO gate — so an AP manager could bulk-approve their
own uploads. The fix routes `approved` targets through review.approve_invoice
(the same path the single-invoice endpoint uses), skipping any invoice that
fails a control instead of aborting the batch.

The same hole existed on the other branch and on the payment path:

- `rejected` was applied with a bare `transition_invoice`, so it wrote no
  `review_rejected` exception, never bumped `rejection_count`, and never cleared
  `WorkflowInstance.state_data["approval_levels"]` — a reworked invoice then
  resumed its approval chain at the level it was rejected at, counting a
  pre-correction approval as still valid. It now routes through
  `review.reject_invoice`, the single reject chokepoint that clears the chain.
- `payment_scheduled` (a legal edge off `approved`) let a bulk call mark invoices
  scheduled with no PaymentRun / Payment behind them, past the payment-blocking
  exception gate, the CFO threshold, run segregation and the sanctions screen —
  and freeze them into IMMUTABLE_STATUSES with no payment to void. It and the
  other system-driven states are now refused with a 422.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog, WorkflowInstance


async def _seed(mk, org_id, *, number, uploaded_by_id=None, status=InvoiceStatus.ready_for_review):
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                invoice_number=number,
                vendor_name="Bulk Vendor",
                amount=Decimal("500.00"),
                currency="USD",
                status=status,
                uploaded_by_id=uploaded_by_id,
            )
        )
        await s.commit()
    return inv_id


@pytest.mark.asyncio
async def test_bulk_approve_of_own_upload_is_skipped_by_segregation(realdb):
    info = realdb.info("a")
    actor_id = info.users["ap_manager"]
    # Invoice the acting ap_manager uploaded — segregation must block self-approve.
    inv_id = await _seed(
        realdb.sessionmaker("a"), info.org_id, number="BULK-SELF", uploaded_by_id=actor_id
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": "approved"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 0
    assert str(inv_id) in body["skipped"]

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.ready_for_review, "self-approve must be blocked"
        approved_rows = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id, AuditLog.action == "invoice.approved")
            )
        ).scalar_one()
    assert approved_rows == 0


@pytest.mark.asyncio
async def test_bulk_approve_of_other_invoice_runs_real_approve_path(realdb):
    info = realdb.info("a")
    # uploaded_by_id NULL → segregation doesn't fire; the approve path runs fully.
    inv_id = await _seed(realdb.sessionmaker("a"), info.org_id, number="BULK-OK")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": "approved"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.approved
        # It went through review.approve_invoice → an `invoice.approved` audit row
        # (with the approval signature), NOT the old `invoice.bulk_status_change`.
        approved_rows = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id, AuditLog.action == "invoice.approved")
            )
        ).scalar_one()
    assert approved_rows == 1


# ---------------------------------------------------------------------------
# `rejected` must route through review.reject_invoice, not a bare transition
# ---------------------------------------------------------------------------


async def _seed_with_instance(mk, org_id, *, number, state_data):
    """Invoice in `ready_for_review` plus a WorkflowInstance carrying chain state."""
    from app.models.workflow import WorkflowDefinition

    inv_id = uuid.uuid4()
    snapshot = {"steps": [{"number": 2, "type": "approval", "enabled": True}]}
    async with mk() as s:
        defn = WorkflowDefinition(
            name=f"Bulk WF {number}",
            steps_config=snapshot,
            is_active=False,
            is_default=False,
            organization_id=org_id,
        )
        s.add(defn)
        await s.flush()
        inv = Invoice(
            id=inv_id,
            organization_id=org_id,
            invoice_number=number,
            vendor_name="Chain Vendor",
            amount=Decimal("500.00"),
            currency="USD",
            status=InvoiceStatus.ready_for_review,
        )
        s.add(inv)
        await s.flush()
        s.add(
            WorkflowInstance(
                correlation_id=inv.correlation_id,
                definition_id=defn.id,
                invoice_id=inv.id,
                current_step=1,
                state="active",
                steps_config_snapshot=snapshot,
                state_data=state_data,
            )
        )
        await s.commit()
    return inv_id


@pytest.mark.asyncio
async def test_bulk_reject_clears_approval_chain_and_raises_exception(realdb):
    from app.models.exception import Exception as ExceptionModel

    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_with_instance(
        mk,
        info.org_id,
        number=f"BULK-REJ-{uuid.uuid4().hex[:8]}",
        state_data={"approval_levels": [{"name": "Manager", "approvals": ["someone"]}]},
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": "rejected", "reason": "Wrong PO"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.rejected
        instance = (
            await s.execute(select(WorkflowInstance).where(WorkflowInstance.invoice_id == inv_id))
        ).scalar_one()
        # The single reject chokepoint clears the chain so a reworked invoice
        # re-runs every level instead of resuming past a stale approval.
        assert "approval_levels" not in (instance.state_data or {})
        assert (instance.state_data or {}).get("rejection_count") == 1

        exc_rows = (
            await s.execute(
                select(func.count())
                .select_from(ExceptionModel)
                .where(
                    ExceptionModel.invoice_id == inv_id,
                    ExceptionModel.exception_type == "review_rejected",
                )
            )
        ).scalar_one()
        assert exc_rows == 1

        rejected_rows = (
            await s.execute(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.entity_id == inv_id, AuditLog.action == "invoice.rejected")
            )
        ).scalar_one()
        assert rejected_rows == 1


@pytest.mark.asyncio
async def test_bulk_reject_without_reason_is_422(realdb):
    info = realdb.info("a")
    inv_id = await _seed(
        realdb.sessionmaker("a"), info.org_id, number=f"BULK-NOREASON-{uuid.uuid4().hex[:8]}"
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": "rejected"},
        )
    assert resp.status_code == 422, resp.text
    assert "rejection reason" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# System-driven states are refused outright
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    ["payment_scheduled", "paid", "sending_to_erp", "sent_to_erp", "posted_in_erp", "failed"],
)
async def test_bulk_status_refuses_system_driven_targets(realdb, target):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed(
        mk,
        info.org_id,
        number=f"BULK-SYS-{target}-{uuid.uuid4().hex[:6]}",
        status=InvoiceStatus.approved,
    )

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": target},
        )
    assert resp.status_code == 422, resp.text
    assert target in resp.json()["detail"]

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        # Untouched — in particular an `approved` invoice is NOT frozen into
        # IMMUTABLE_STATUSES with no Payment behind it.
        assert inv.status == InvoiceStatus.approved


@pytest.mark.asyncio
async def test_bulk_status_payment_scheduled_creates_no_payment_and_is_refused(realdb):
    """The `approved → payment_scheduled` edge is legal in the state machine, so
    only the endpoint whitelist stops this one."""
    from app.models.payment import Payment

    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed(
        mk,
        info.org_id,
        number=f"BULK-SCHED-{uuid.uuid4().hex[:8]}",
        status=InvoiceStatus.approved,
    )

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": "payment_scheduled"},
        )
    assert resp.status_code == 422, resp.text

    async with mk() as s:
        payments = (
            await s.execute(
                select(func.count()).select_from(Payment).where(Payment.invoice_id == inv_id)
            )
        ).scalar_one()
    assert payments == 0


# ---------------------------------------------------------------------------
# Allowed queue targets still work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_resubmit_reopens_a_review_step(realdb):
    """`rejected → ready_for_review` is a resubmit; its owner opens the new
    review WorkflowStep the approval queue works from."""
    from app.models.workflow import WorkflowStep

    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_with_instance(
        mk, info.org_id, number=f"BULK-RESUB-{uuid.uuid4().hex[:8]}", state_data={}
    )
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        inv.status = InvoiceStatus.rejected
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices/bulk/status",
            json={"ids": [str(inv_id)], "status": "ready_for_review"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"] == 1

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        assert inv.status == InvoiceStatus.ready_for_review
        instance = (
            await s.execute(select(WorkflowInstance).where(WorkflowInstance.invoice_id == inv_id))
        ).scalar_one()
        steps = (
            (await s.execute(select(WorkflowStep).where(WorkflowStep.instance_id == instance.id)))
            .scalars()
            .all()
        )
    assert any(s_.step_type == "approval" and s_.completed_at is None for s_ in steps), steps
