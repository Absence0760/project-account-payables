"""The triggering user's real roles must reach the resolver (and thus
``approve_invoice``), not a hardcoded ``{"ap_manager"}`` set.

Before this fix all four auto-fix resolvers called
``approve_invoice(actor_roles={"ap_manager"})`` regardless of who triggered the
run — so a CFO-gated invoice resolved by a CFO was wrongly blocked, and the
audit trail's authoriser role diverged from the ``actor_id`` it recorded.

DB-free: the session, the locked-row read, and the invoice fetch are mocked;
we assert the coordinator threads ``actor_roles`` into ``resolver.apply``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.exception_agents.base import ACTION_AUTO_RESOLVED, AgentEvaluation
from app.services.exception_agents.coordinator import run_agent


def _exception():
    return SimpleNamespace(
        id=uuid.uuid4(),
        status="open",
        exception_type="po_mismatch",
        organization_id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),
        created_at=None,
        resolution=None,
        resolved_by=None,
        resolved_at=None,
        time_to_resolution_seconds=None,
    )


def _mock_db(exc, invoice):
    """db.execute is awaited twice: the locked-exception read, then the invoice
    fetch. Each returns a result whose .scalar_one() yields the row."""
    exc_res = MagicMock()
    exc_res.scalar_one = MagicMock(return_value=exc)
    inv_res = MagicMock()
    inv_res.scalar_one = MagicMock(return_value=invoice)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[exc_res, inv_res])
    db.add = MagicMock()
    return db


@pytest.mark.asyncio
async def test_actor_roles_threaded_into_resolver_apply():
    exc = _exception()
    invoice = SimpleNamespace(id=exc.invoice_id, entity_id=None)
    db = _mock_db(exc, invoice)

    captured: dict = {}

    class _FakeResolver:
        agent_type = "fake_v1"

        async def evaluate(self, _db, *, exception, invoice, org_settings):
            return AgentEvaluation(
                recommended_action=ACTION_AUTO_RESOLVED,
                confidence=Decimal("1"),
                rationale="ok",
                changes={},
            )

        async def apply(
            self, _db, *, exception, invoice, evaluation, actor_id, actor_roles=None
        ):
            captured["actor_roles"] = actor_roles
            captured["actor_id"] = actor_id

    with patch(
        "app.services.exception_agents.coordinator.get_resolver",
        return_value=_FakeResolver(),
    ):
        actor = uuid.uuid4()
        await run_agent(
            db,
            exception=exc,
            actor_id=actor,
            org_settings={"exception_agents": {"autonomy_level": "aggressive"}},
            actor_roles={"cfo"},
        )

    assert captured["actor_roles"] == {"cfo"}
    assert captured["actor_id"] == actor
