"""Service-level regression tests for `services.review.approve_invoice`.

Covers two correctness bugs in the approval orchestration that the
per-helper unit tests (`test_approval_thresholds.py`,
`test_approval_chain.py`) could not catch because they exercise the
helpers in isolation, not the order `approve_invoice` calls them in:

  - BUG 4 — approve-with-corrections must enforce the max-amount cap and
    the CFO gate against the POST-correction amount. A reviewer must not
    be able to approve a $100 invoice with corrections={"amount": 5000}
    past a $1000 cap (→ 422) or a $500 CFO gate (→ 403). Regression:
    thresholds used to run BEFORE corrections were applied, reading the
    stale pre-correction amount.

  - BUG 8 — every approval decision in a multi-level `chain` must write
    an append-only audit row, not just the chain-completing one. A
    level-1-of-N approval must produce an `invoice.approval_step` audit
    row capturing the actor + level. Regression: intermediate approvals
    only mutated `state_data` JSONB and returned early with no audit row,
    so an auditor could never reconstruct who approved at levels 1..N-1.

DB-free: the session, the workflow-instance lookup, the row lock, the
correction cache, and the RAG embedding are all mocked, mirroring the
existing approval unit tests. We assert on the control flow
(raises / audit dispatch), not on a committed Postgres row.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_invoice(*, amount, uploaded_by_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        entity_id=None,
        amount=Decimal(str(amount)),
        vendor_name="Vendor",
        uploaded_by_id=uploaded_by_id,
        file_key=None,
        approval_date=None,
        approved_by=None,
    )


def _instance(snapshot: dict | None, *, state_data=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        steps_config_snapshot=snapshot,
        state_data=state_data,
    )


def _single_level_snapshot(config: dict) -> dict:
    """A snapshot whose approval step carries the given config (no chain)."""
    return {"steps": [{"type": "approval", "config": config}]}


# ---------------------------------------------------------------------------
# BUG 4 — corrections applied before threshold / CFO enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrected_amount_over_cap_is_rejected_422():
    """approve(amount=100, corrections={"amount": 5000}) against a $1000 cap
    must 422 on the CORRECTED amount, not pass on the stale $100."""
    from app.services import review

    invoice = _make_invoice(amount=100)
    instance = _instance(_single_level_snapshot({"max_invoice_amount": 1000}))
    db = AsyncMock()

    with (
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "record_corrections", new=AsyncMock()),
        patch.object(review, "store_embedding", new=AsyncMock()),
        patch.object(review, "_fetch_invoice_bytes", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await review.approve_invoice(
                db,
                invoice,
                actor_id=uuid.uuid4(),
                actor_name="Manager",
                actor_roles={"ap_manager"},
                corrections={"amount": "5000"},
            )

    assert exc_info.value.status_code == 422
    # And the correction was actually applied (the gate read the new amount).
    assert Decimal(str(invoice.amount)) == Decimal("5000")
    # Rejected before finalize — no approval stamp.
    assert invoice.approval_date is None
    assert invoice.approved_by is None


@pytest.mark.asyncio
async def test_corrected_amount_over_cfo_gate_is_rejected_403():
    """approve(amount=100, corrections={"amount": 5000}) past a $500 CFO gate,
    by a non-CFO actor, must 403 on the CORRECTED amount."""
    from app.services import review

    invoice = _make_invoice(amount=100)
    instance = _instance(_single_level_snapshot({"require_cfo_above": 500}))
    db = AsyncMock()

    with (
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "record_corrections", new=AsyncMock()),
        patch.object(review, "store_embedding", new=AsyncMock()),
        patch.object(review, "_fetch_invoice_bytes", new=AsyncMock(return_value=None)),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await review.approve_invoice(
                db,
                invoice,
                actor_id=uuid.uuid4(),
                actor_name="Manager",
                actor_roles={"ap_manager"},  # NOT cfo
                corrections={"amount": "5000"},
            )

    assert exc_info.value.status_code == 403
    assert "CFO" in exc_info.value.detail
    assert Decimal(str(invoice.amount)) == Decimal("5000")
    assert invoice.approval_date is None


@pytest.mark.asyncio
async def test_corrected_amount_under_cap_still_approves():
    """A correction that stays UNDER the cap approves normally — the new
    ordering must not block legitimate corrected approvals."""
    from app.services import review

    invoice = _make_invoice(amount=100)
    instance = _instance(
        _single_level_snapshot({"max_invoice_amount": 1000, "require_cfo_above": 500})
    )
    db = AsyncMock()

    with (
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "record_corrections", new=AsyncMock()),
        patch.object(review, "store_embedding", new=AsyncMock()),
        patch.object(review, "_fetch_invoice_bytes", new=AsyncMock(return_value=None)),
        patch.object(review, "transition_invoice", new=AsyncMock()) as mock_transition,
        patch.object(review, "advance_workflow", new=AsyncMock()),
    ):
        # corrected to 250 — under both the 1000 cap and the 500 CFO gate.
        await review.approve_invoice(
            db,
            invoice,
            actor_id=uuid.uuid4(),
            actor_name="Manager",
            actor_roles={"ap_manager"},
            corrections={"amount": "250"},
        )

    assert Decimal(str(invoice.amount)) == Decimal("250")
    assert invoice.approval_date is not None  # finalize ran
    mock_transition.assert_awaited_once()


# ---------------------------------------------------------------------------
# BUG 8 — intermediate chain approvals write an audit row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_intermediate_chain_approval_writes_audit_row():
    """The first approval in a 2-level chain stays in review (chain not yet
    complete) but MUST still write an `invoice.approval_step` audit row
    capturing the actor + the level approved."""
    from app.services import review

    actor_id = uuid.uuid4()
    invoice = _make_invoice(amount=1000)
    snapshot = _single_level_snapshot(
        {
            "approver_strategy": "chain",
            "approval_chain": [
                {"name": "L1", "required_approvals": 1, "approver_ids": []},
                {"name": "L2", "required_approvals": 1, "approver_ids": []},
            ],
        }
    )
    instance = _instance(snapshot, state_data=None)

    # The chain path re-fetches the instance under a row lock via db.execute().
    locked_result = MagicMock()
    locked_result.scalar_one = MagicMock(return_value=instance)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=locked_result)

    captured: list[dict] = []

    async def _capture_audit(_db, **kwargs):
        captured.append(kwargs)

    with (
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "record_corrections", new=AsyncMock()),
        patch.object(review, "store_embedding", new=AsyncMock()),
        patch.object(review, "_fetch_invoice_bytes", new=AsyncMock(return_value=None)),
        patch.object(review, "transition_invoice", new=AsyncMock()) as mock_transition,
        patch.object(review, "advance_workflow", new=AsyncMock()),
        patch("app.services.audit_dispatch.dispatch_audit", new=_capture_audit),
    ):
        result = await review.approve_invoice(
            db,
            invoice,
            actor_id=actor_id,
            actor_name="Approver One",
            actor_roles={"ap_manager"},
        )

    # Chain NOT complete on the first approval — no finalize, no terminal
    # invoice.approved transition.
    assert result is invoice
    assert invoice.approval_date is None
    mock_transition.assert_not_awaited()

    # But a partial-approval audit row WAS written.
    step_rows = [r for r in captured if r.get("action") == "invoice.approval_step"]
    assert len(step_rows) == 1, "level-1 approval must write exactly one audit row"
    row = step_rows[0]
    assert row["actor_id"] == actor_id
    assert row["entity_id"] == invoice.id
    assert row["entity_type"] == "invoice"
    assert row["details"]["decision"] == "approved"
    assert row["details"]["level"] == 0  # the first level


@pytest.mark.asyncio
async def test_completing_chain_approval_writes_final_approved_row_not_step():
    """The chain-COMPLETING approval finalizes (transition → invoice.approved)
    and must NOT emit a partial `invoice.approval_step` row for that decision."""
    from app.services import review
    from app.services.approval_chain import advance_approval_chain, init_chain_state

    invoice = _make_invoice(amount=1000)
    snapshot = _single_level_snapshot(
        {
            "approver_strategy": "chain",
            "approval_chain": [
                {"name": "L1", "required_approvals": 1, "approver_ids": []},
                {"name": "L2", "required_approvals": 1, "approver_ids": []},
            ],
        }
    )
    instance = _instance(snapshot, state_data=None)
    # Pre-advance to level 1 (level 0 already approved by someone else).
    init_chain_state(
        instance,
        [
            {"name": "L1", "required_approvals": 1, "approver_ids": []},
            {"name": "L2", "required_approvals": 1, "approver_ids": []},
        ],
    )
    advance_approval_chain(instance, uuid.uuid4())  # now on level 1, not complete

    locked_result = MagicMock()
    locked_result.scalar_one = MagicMock(return_value=instance)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=locked_result)

    captured: list[dict] = []

    async def _capture_audit(_db, **kwargs):
        captured.append(kwargs)

    with (
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "record_corrections", new=AsyncMock()),
        patch.object(review, "store_embedding", new=AsyncMock()),
        patch.object(review, "_fetch_invoice_bytes", new=AsyncMock(return_value=None)),
        patch.object(review, "transition_invoice", new=AsyncMock()) as mock_transition,
        patch.object(review, "advance_workflow", new=AsyncMock()),
        patch("app.services.audit_dispatch.dispatch_audit", new=_capture_audit),
    ):
        await review.approve_invoice(
            db,
            invoice,
            actor_id=uuid.uuid4(),
            actor_name="Approver Two",
            actor_roles={"ap_manager"},
        )

    # The completing approval finalizes via transition_invoice (which is what
    # writes invoice.approved) and emits no partial step row of its own.
    mock_transition.assert_awaited_once()
    assert invoice.approval_date is not None
    step_rows = [r for r in captured if r.get("action") == "invoice.approval_step"]
    assert step_rows == [], "the completing approval must not write a partial step row"


# ---------------------------------------------------------------------------
# refresh_warnings after approve-with-corrections (stale po_match / warnings)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_corrections_rerun_refresh_warnings():
    """approve-with-corrections must recompute warnings + po_match against the
    corrected fields, else a po_number/vendor correction leaves stale artefacts
    (a failed match against the old PO, a missed duplicate) on the approved row.
    The refresh runs with the invoice AND the passed org_settings."""
    from app.services import review

    invoice = _make_invoice(amount=250)
    instance = _instance(_single_level_snapshot({}))
    db = AsyncMock()
    org_settings = {"fraud_rules": {"round_amount_enabled": False}}

    captured: dict = {}

    async def _capture_refresh(_db, inv, *, org_settings=None):
        captured["invoice"] = inv
        captured["org_settings"] = org_settings

    with (
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "record_corrections", new=AsyncMock()),
        patch.object(review, "store_embedding", new=AsyncMock()),
        patch.object(review, "_fetch_invoice_bytes", new=AsyncMock(return_value=None)),
        patch.object(review, "transition_invoice", new=AsyncMock()),
        patch.object(review, "advance_workflow", new=AsyncMock()),
        patch("app.services.invoice_warnings.refresh_warnings", new=_capture_refresh),
    ):
        await review.approve_invoice(
            db,
            invoice,
            actor_id=uuid.uuid4(),
            actor_name="Manager",
            actor_roles={"ap_manager"},
            corrections={"po_number": "PO-123"},
            org_settings=org_settings,
        )

    assert captured["invoice"] is invoice
    assert captured["org_settings"] is org_settings
    # The correction was applied before the refresh ran.
    assert invoice.po_number == "PO-123"


@pytest.mark.asyncio
async def test_no_corrections_does_not_rerun_refresh_warnings():
    """With no corrections there's nothing to make the persisted warnings stale,
    so the (non-trivial) refresh is skipped — the approve path stays cheap."""
    from app.services import review

    invoice = _make_invoice(amount=250)
    instance = _instance(_single_level_snapshot({}))
    db = AsyncMock()

    calls: list = []

    async def _refresh(_db, inv, *, org_settings=None):
        calls.append(inv)

    with (
        patch.object(review, "get_workflow_instance", new=AsyncMock(return_value=instance)),
        patch.object(review, "record_corrections", new=AsyncMock()),
        patch.object(review, "store_embedding", new=AsyncMock()),
        patch.object(review, "_fetch_invoice_bytes", new=AsyncMock(return_value=None)),
        patch.object(review, "transition_invoice", new=AsyncMock()),
        patch.object(review, "advance_workflow", new=AsyncMock()),
        patch("app.services.invoice_warnings.refresh_warnings", new=_refresh),
    ):
        await review.approve_invoice(
            db,
            invoice,
            actor_id=uuid.uuid4(),
            actor_name="Manager",
            actor_roles={"ap_manager"},
        )

    assert calls == []
