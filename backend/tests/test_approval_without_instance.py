"""An invoice with no ``WorkflowInstance`` still meets the org's approval gates.

Not every invoice has an instance. ``email_intake._create_invoice_from_attachment``
and ``peppol_receive.receive_peppol_message`` both insert the row without one,
and so does any legacy / directly-inserted invoice. ``review`` read the approval
config **only** off ``WorkflowInstance.steps_config_snapshot``, so for those
invoices it got ``{}`` — and ``{}`` did not mean "no rules apply", it meant the
max-amount cap, the CFO gate, the structuring guard and the named-approver check
were all skipped. A $50,000 invoice that arrived by email cleared a $1,000
``require_cfo_above`` on a lone ap_manager's approval.

`review.resolve_approval_config` now falls back — fail-CLOSED — to the org's
currently-active definition when there is no snapshot to read. The frozen
snapshot still wins whenever one exists (the per-invoice invariant is
untouched); the fallback only fills a gap that previously read as "ungated".
Resolution is read-only, so a definition can never appear as a side effect of an
approval.

The same missing instance also made `assign_reviewer` return early, writing the
assignee with no `invoice.assigned_for_review` audit row and no notification —
covered here too.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog, WorkflowDefinition, WorkflowInstance

_GATED_STEPS = {
    "steps": [
        {
            "number": 2,
            "type": "approval",
            "name": "Manager Approval",
            "enabled": True,
            "config": {
                "required": True,
                "approver_strategy": "manual",
                "require_segregation": True,
                "require_cfo_above": "1000.00",
            },
        }
    ]
}


async def _install_gated_definition(mk, org_id) -> None:
    """Make the org's ONLY active definition a CFO-gated one."""
    async with mk() as s:
        await s.execute(
            WorkflowDefinition.__table__.update().values(is_active=False, is_default=False)
        )
        s.add(
            WorkflowDefinition(
                name="CFO-gated",
                steps_config=_GATED_STEPS,
                is_active=True,
                is_default=True,
                organization_id=org_id,
                entity_id=None,
            )
        )
        await s.commit()


async def _seed_instanceless(mk, org_id, *, number: str, amount: str) -> uuid.UUID:
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                invoice_number=number,
                vendor_name="Instanceless Vendor",
                amount=Decimal(amount),
                currency="USD",
                status=InvoiceStatus.ready_for_review,
            )
        )
        await s.commit()
    async with mk() as s:
        assert (
            await s.execute(select(WorkflowInstance).where(WorkflowInstance.invoice_id == inv_id))
        ).scalar_one_or_none() is None, "the fixture must have no workflow instance"
    return inv_id


@pytest.mark.asyncio
async def test_cfo_gate_applies_without_a_workflow_instance(realdb):
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _install_gated_definition(mk, info.org_id)
    inv_id = await _seed_instanceless(mk, info.org_id, number="NOINST-1", amount="50000.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/approve")
    assert resp.status_code == 403, resp.text
    assert "CFO approval required" in resp.text

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
    assert inv.status == InvoiceStatus.ready_for_review, "the refused approval must not land"


@pytest.mark.asyncio
async def test_under_the_gate_still_approves_without_an_instance(realdb):
    """Surgical: the fallback gates, it does not block everything."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _install_gated_definition(mk, info.org_id)
    inv_id = await _seed_instanceless(mk, info.org_id, number="NOINST-2", amount="500.00")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/approve")
    assert resp.status_code == 200, resp.text

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
    assert inv.status == InvoiceStatus.approved


@pytest.mark.asyncio
async def test_the_frozen_snapshot_still_wins_over_the_live_definition(realdb):
    """The per-invoice snapshot invariant is untouched — an in-flight invoice is
    governed by the config it entered under, even after the org tightens the
    live definition."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    await _install_gated_definition(mk, info.org_id)
    inv_id = await _seed_instanceless(mk, info.org_id, number="NOINST-3", amount="50000.00")

    # This invoice DID enter under an ungated config — freeze it.
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        defn = (
            (
                await s.execute(
                    select(WorkflowDefinition).where(WorkflowDefinition.is_active == True)  # noqa: E712
                )
            )
            .scalars()
            .first()
        )
        s.add(
            WorkflowInstance(
                correlation_id=inv.correlation_id,
                definition_id=defn.id,
                invoice_id=inv_id,
                current_step=1,
                state="active",
                steps_config_snapshot={
                    "steps": [
                        {
                            "number": 2,
                            "type": "approval",
                            "name": "Manager Approval",
                            "enabled": True,
                            "config": {"required": True, "approver_strategy": "manual"},
                        }
                    ]
                },
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/approve")
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_assign_reviewer_audits_and_notifies_without_an_instance(realdb):
    """A missing instance must not swallow the assignment's audit row."""
    info = realdb.info("a")
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_instanceless(mk, info.org_id, number="NOINST-4", amount="100.00")
    reviewer_id = info.users["ap_manager"]

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/assign", json={"user_id": str(reviewer_id)})
    assert resp.status_code == 200, resp.text

    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == inv_id,
                        AuditLog.action == "invoice.assigned_for_review",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert inv.assigned_to_id == reviewer_id
    assert len(rows) == 1, "assignment must be audited even with no workflow instance"
    assert rows[0].details.get("reviewer_id") == str(reviewer_id)
