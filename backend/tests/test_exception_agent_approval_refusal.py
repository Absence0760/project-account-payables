"""An approval refusal inside ``approve_invoice`` must escalate, not 403 out.

Every resolver's ``apply`` ends in ``review.approve_invoice``, which enforces
controls the resolver's own pre-checks do not: segregation of duties (the
uploader can't approve), the named-approver gate, and the max-amount / CFO
thresholds measured against the same-vendor rolling AGGREGATE (a resolver only
sees the single invoice). Each refusal is an ``HTTPException``.

The coordinator only caught ``NotApprovable``, so those propagated out of
``run_agent`` to the route: an AP manager who resolved an exception on an
invoice they had uploaded themselves got a bare 403, with the exception left
``open``, NO ``AgentDecision`` row, and nothing in the queue saying why — while
every other way an apply can fail records a decision and escalates.

The apply also runs in a SAVEPOINT now, because ``approve_invoice`` applies
``corrections`` (including ``amount``) BEFORE it enforces the thresholds. A
threshold refusal after an amount correction would otherwise be committed by the
escalation that follows — the agent's amount change persisted on an invoice
nobody approved.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from app.services.exception_agents.base import ACTION_AUTO_RESOLVED, AgentEvaluation
from app.services.exception_agents.coordinator import run_agent


def _exception():
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="open",
        exception_type="po_mismatch",
        severity="warning",
        organization_id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        created_at=None,
        resolution=None,
        resolved_by=None,
        resolved_at=None,
        time_to_resolution_seconds=None,
    )


class _FakeSavepoint:
    def __init__(self):
        self.entered = False
        self.rolled_back = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.rolled_back = exc_type is not None
        return False


def _mock_db(exc, invoice):
    exc_res = MagicMock()
    exc_res.scalar_one = MagicMock(return_value=exc)
    inv_res = MagicMock()
    inv_res.scalar_one = MagicMock(return_value=invoice)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[exc_res, inv_res])
    db.add = MagicMock()
    db.savepoint = _FakeSavepoint()
    db.begin_nested = MagicMock(return_value=db.savepoint)
    return db


def _resolver_raising(exc_to_raise):
    class _Resolver:
        agent_type = "fake_v1"

        async def evaluate(self, _db, *, exception, invoice, org_settings):
            return AgentEvaluation(
                recommended_action=ACTION_AUTO_RESOLVED,
                confidence=Decimal("1"),
                rationale="ok",
                changes={},
            )

        async def apply(self, *a, **kw):
            raise exc_to_raise

    return _Resolver()


async def _run(resolver):
    exc = _exception()
    invoice = SimpleNamespace(id=exc.invoice_id, entity_id=None, correlation_id=uuid.uuid4())
    db = _mock_db(exc, invoice)
    with patch("app.services.exception_agents.coordinator.get_resolver", return_value=resolver):
        result = await run_agent(
            db,
            exception=exc,
            actor_id=uuid.uuid4(),
            org_settings={"exception_agents": {"autonomy_level": "aggressive"}},
            actor_roles={"ap_manager"},
        )
    return exc, db, result


@pytest.mark.asyncio
async def test_segregation_refusal_escalates_with_a_recorded_decision():
    """The common trigger: the triggering user uploaded the invoice."""
    exc, db, result = await _run(
        _resolver_raising(
            HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Segregation of duties: the user who uploaded this invoice "
                    "cannot also approve it."
                ),
            )
        )
    )
    assert exc.status == "escalated"
    assert result.decision.action_taken == "escalated"
    # The human picking it up reads WHY, in the queue.
    assert "Segregation of duties" in result.decision.rationale
    assert result.decision.rationale.startswith("Could not auto-approve:")
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_threshold_refusal_escalates_and_unwinds_the_partial_apply():
    """`approve_invoice` applies corrections BEFORE enforcing the money gates."""
    exc, db, result = await _run(
        _resolver_raising(
            HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invoices above $10,000.00 require CFO approval.",
            )
        )
    )
    assert exc.status == "escalated"
    assert result.decision.action_taken == "escalated"
    assert "CFO approval" in result.decision.rationale
    # The savepoint was entered and unwound, so nothing the refused apply wrote
    # survives into the escalation's commit.
    assert db.savepoint.entered is True
    assert db.savepoint.rolled_back is True


@pytest.mark.asyncio
async def test_a_non_string_detail_still_yields_a_usable_rationale():
    exc, _db, result = await _run(
        _resolver_raising(HTTPException(status_code=422, detail={"code": "nope"}))
    )
    assert exc.status == "escalated"
    assert result.decision.rationale == (
        "Could not auto-approve: The approval was refused. Escalated to a human."
    )


@pytest.mark.asyncio
async def test_a_server_error_is_not_swallowed_as_an_escalation():
    """A 5xx is a fault, not a refusal — it must not be recorded as a decision."""
    exc = _exception()
    invoice = SimpleNamespace(id=exc.invoice_id, entity_id=None, correlation_id=uuid.uuid4())
    db = _mock_db(exc, invoice)
    with patch(
        "app.services.exception_agents.coordinator.get_resolver",
        return_value=_resolver_raising(HTTPException(status_code=503, detail="upstream down")),
    ):
        with pytest.raises(HTTPException) as caught:
            await run_agent(
                db,
                exception=exc,
                actor_id=uuid.uuid4(),
                org_settings={"exception_agents": {"autonomy_level": "aggressive"}},
                actor_roles={"ap_manager"},
            )
    assert caught.value.status_code == 503
    assert exc.status == "open"
    db.commit.assert_not_awaited()
