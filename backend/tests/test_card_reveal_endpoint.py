"""HTTP-boundary tests for the single-use card-reveal endpoint.

``test_card_reveal.py`` covers the service layer (mint/consume branches)
against a mocked session. This file covers the ``GET /portal/cards/{token}``
*handler* — the glue the service tests never reach:

  * consume-reason -> HTTP-status mapping (invalid 404, expired 410, used 410)
  * the append-only audit row written on a successful reveal
    (``card.revealed_via_token``, actor_id=None) — a regression that drops
    the dispatch would otherwise be invisible
  * PII suppression on the two degraded paths (org disabled cards after
    issuance; adapter outage) — pan/cvv must come back None, and the token
    must still be consumed (committed) without an audit/PAN leak

The handler imports its collaborators inside the function from their source
modules, so we patch them at the source. The tenant ``db`` and the
control-plane session are mocked — matching the suite's unit-test
convention.

Out of scope here (covered elsewhere, by design):
  * single-use durability across a real commit+re-read — the ``used_at``
    flip is asserted in ``test_card_reveal.py`` against the service.
  * cross-tenant token reveal — the token lookup runs on the tenant ``db``
    resolved by ``get_tenant_db``, whose isolation chokepoint is tested in
    ``test_tenant_isolation.py``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.portal import reveal_card


def _card(**overrides) -> SimpleNamespace:
    base = dict(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        vendor_id=uuid.uuid4(),
        correlation_id=uuid.uuid4(),
        last_four="4321",
        amount_limit=Decimal("100.00"),
        currency="USD",
        expires_at=None,
        provider_card_id="mock_card_xyz",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _FakeCtrl:
    """Async-context control-plane session returning a fixed org row."""

    def __init__(self, org) -> None:
        self._org = org

    async def __aenter__(self) -> _FakeCtrl:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def execute(self, *_a, **_k):
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=self._org)
        return result


@contextmanager
def _patched(
    *,
    consume_return,
    config=None,
    details=None,
    adapter_raises=False,
):
    """Patch every collaborator reveal_card imports inside its body."""
    # Build the tenant session explicitly (only execute/commit are used) so a
    # bare AsyncMock doesn't auto-spawn unawaited child coroutines at GC time.
    db = MagicMock()
    vendor_result = MagicMock()
    vendor_result.scalar_one_or_none = MagicMock(return_value=None)
    db.execute = AsyncMock(return_value=vendor_result)
    db.commit = AsyncMock()

    dispatch_audit = AsyncMock()
    org = SimpleNamespace(settings={"cards": {"enabled": True}})

    adapter = MagicMock()
    if adapter_raises:
        adapter.get_card_details = AsyncMock(side_effect=RuntimeError("provider down"))
    else:
        adapter.get_card_details = AsyncMock(return_value=details)

    with (
        patch(
            "app.services.card_reveal.consume_reveal_token",
            AsyncMock(return_value=consume_return),
        ),
        patch("app.database.control_session_factory", MagicMock(return_value=_FakeCtrl(org))),
        patch("app.services.card_issuance._resolve_card_config", MagicMock(return_value=config)),
        patch("app.services.card_adapters.get_card_adapter", MagicMock(return_value=adapter)),
        patch("app.services.audit_dispatch.dispatch_audit", dispatch_audit),
    ):
        yield SimpleNamespace(db=db, dispatch_audit=dispatch_audit, adapter=adapter)


# ---------------------------------------------------------------------------
# consume-reason -> HTTP-status mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "status"),
    [("invalid", 404), ("expired", 410), ("used", 410)],
)
async def test_reveal_maps_consume_reason_to_status(reason, status):
    with _patched(consume_return=(None, reason)) as h:
        with pytest.raises(HTTPException) as exc:
            await reveal_card(token="tok", db=h.db)
    assert exc.value.status_code == status
    # No PAN reveal and no audit row on any error path.
    h.dispatch_audit.assert_not_awaited()
    h.db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# success — PAN returned once + audit row written
# ---------------------------------------------------------------------------


async def test_reveal_success_returns_pan_and_writes_audit():
    card = _card()
    details = SimpleNamespace(pan="4111111111114321", cvv="123")
    with _patched(
        consume_return=(card, None),
        config={"provider": "mock"},
        details=details,
    ) as h:
        body = await reveal_card(token="tok", db=h.db)

    assert body["pan"] == "4111111111114321"
    assert body["cvv"] == "123"
    assert body["last_four"] == "4321"
    assert body["amount_limit"] == 100.0

    h.dispatch_audit.assert_awaited_once()
    kwargs = h.dispatch_audit.await_args.kwargs
    assert kwargs["action"] == "card.revealed_via_token"
    assert kwargs["entity_type"] == "virtual_card"
    assert kwargs["entity_id"] == card.id
    assert kwargs["actor_id"] is None
    assert kwargs["organization_id"] == card.organization_id
    h.db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# degraded paths — PII suppressed, token still consumed, no audit/PAN leak
# ---------------------------------------------------------------------------


async def test_reveal_suppresses_pan_when_cards_disabled():
    """Org turned cards off after issuance (config resolves to None)."""
    card = _card()
    with _patched(consume_return=(card, None), config=None) as h:
        body = await reveal_card(token="tok", db=h.db)

    assert body["pan"] is None
    assert body["cvv"] is None
    assert body["warning"]
    assert body["last_four"] == "4321"
    # Degraded path must not emit a reveal audit row, but must consume the token.
    h.dispatch_audit.assert_not_awaited()
    h.db.commit.assert_awaited_once()


async def test_reveal_suppresses_pan_on_adapter_outage():
    card = _card()
    with _patched(
        consume_return=(card, None),
        config={"provider": "mock"},
        adapter_raises=True,
    ) as h:
        body = await reveal_card(token="tok", db=h.db)

    assert body["pan"] is None
    assert body["cvv"] is None
    assert body["warning"]
    h.dispatch_audit.assert_not_awaited()
    h.db.commit.assert_awaited_once()
