"""Seed workflow back-fill — ``_seed_workflows_for_invoices``.

Regression guard for the "approvals surface is empty in dev" bug: the seed used
to create invoices in ``ready_for_review`` without any ``WorkflowInstance`` /
``WorkflowStep`` rows, so the approval queue and the assistant's
``list_pending_approvals`` tool (which joins to an *active approval step*) read
empty. These pure tests assert the helper now emits the right steps per status —
crucially an ACTIVE approval step assigned to the org admin for
``ready_for_review`` invoices.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.models.invoice import Invoice
from app.models.workflow import WorkflowDefinition, WorkflowInstance, WorkflowStep
from scripts.seed import _seed_workflows_for_invoices

_NOW = datetime(2026, 6, 18, 12, 0, 0, tzinfo=UTC)
_APPROVER = uuid.UUID("00000000-0000-0000-0000-000000000010")


class _FakeSession:
    """Collects ``add``ed ORM objects — the helper only calls ``session.add``."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)


def _default_def() -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        name="Default Workflow",
        steps_config={"steps": [{"number": 1, "type": "extraction"}]},
    )


def _invoice(status: str) -> Invoice:
    return Invoice(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        invoice_number=f"INV-{status}",
        vendor_name="Vendor",
        status=status,
    )


def _run(invoices: list[Invoice]):
    session = _FakeSession()
    _seed_workflows_for_invoices(
        session,
        default_def=_default_def(),
        invoices=invoices,
        approver_id=_APPROVER,
        now=_NOW,
    )
    instances = [o for o in session.added if isinstance(o, WorkflowInstance)]
    steps = [o for o in session.added if isinstance(o, WorkflowStep)]
    return instances, steps


def test_ready_for_review_leaves_active_approval_assigned_to_approver():
    _instances, steps = _run([_invoice("ready_for_review")])
    approval = [s for s in steps if s.step_type == "approval"]
    assert len(approval) == 1
    step = approval[0]
    # Active (incomplete) and assigned to the admin so "my queue" is populated.
    assert step.completed_at is None
    assert step.assigned_to == _APPROVER
    # Back-dated so "approvals sitting > 5 days" demos return it.
    assert (_NOW - step.created_at).days >= 5
    # Extraction precedes it and is completed.
    extraction = [s for s in steps if s.step_type == "extraction"]
    assert len(extraction) == 1
    assert extraction[0].completed_at is not None


def test_approved_has_completed_approval_no_active_step():
    _instances, steps = _run([_invoice("approved")])
    assert {s.step_type for s in steps} == {"extraction", "approval"}
    assert all(s.completed_at is not None for s in steps)


def test_rejected_approval_action_is_reject():
    _instances, steps = _run([_invoice("rejected")])
    approval = next(s for s in steps if s.step_type == "approval")
    assert approval.completed_at is not None
    assert approval.action == "reject"


def test_new_invoice_has_instance_but_no_steps():
    instances, steps = _run([_invoice("new")])
    assert len(instances) == 1
    assert steps == []
    assert instances[0].current_step == 0


def test_pending_has_active_extraction_step():
    _instances, steps = _run([_invoice("pending")])
    assert len(steps) == 1
    assert steps[0].step_type == "extraction"
    assert steps[0].completed_at is None


def test_posted_in_erp_completes_all_three_steps():
    _instances, steps = _run([_invoice("posted_in_erp")])
    assert {s.step_type for s in steps} == {"extraction", "approval", "erp_export"}
    assert all(s.completed_at is not None for s in steps)
    # current_step reflects three completed steps.
    instances, _ = _run([_invoice("posted_in_erp")])
    assert instances[0].current_step == 3


def test_one_instance_per_invoice():
    invoices = [_invoice("new"), _invoice("ready_for_review"), _invoice("paid")]
    instances, _steps = _run(invoices)
    assert len(instances) == 3
    assert {i.invoice_id for i in instances} == {inv.id for inv in invoices}
