"""Tests for the SQS-triggered audit Lambda handler (audit_mode = "lambda").

DB-free: the consumer builds its own engine/session from the message and
calls ``audit.log_action``, so we mock ``create_async_engine``,
``async_sessionmaker`` and ``log_action`` to assert the orchestration —
tenant-DB targeting, UUID coercion, commit/rollback discipline, and the
SQS batch loop — without a live Postgres.

Guards two project invariants that are otherwise unverified in lambda
mode:
  * tenant isolation — the engine is built outside ``get_tenant_db`` from
    the untrusted ``tenant_db_name`` message field, so we lock that the
    URL is derived from exactly that field.
  * append-only audit trail — a well-formed message must produce one
    committed ``log_action`` write, and a failed write must roll back AND
    re-raise so SQS retries/DLQs rather than silently dropping the event.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import audit_lambda

BASE_URL = "postgresql+asyncpg://u:p@host:5432/feohledger"


def _body(**overrides) -> dict:
    """A well-formed SQS message body, with per-test overrides."""
    body = {
        "correlation_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "actor_id": str(uuid.uuid4()),
        "action": "payment.voided",
        "entity_type": "payment",
        "entity_id": str(uuid.uuid4()),
        "details": {"reason": "test"},
        "tenant_db_name": "feoh_acme",
    }
    body.update(overrides)
    return body


class _FakeSession:
    """Async-context-manager session that records commit/rollback."""

    def __init__(self) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False


@contextmanager
def _harness(log_action: AsyncMock | None = None):
    """Patch the module's engine/session factory + audit.log_action."""
    session = _FakeSession()
    engine = MagicMock(dispose=AsyncMock())
    factory = MagicMock(return_value=session)
    la = log_action if log_action is not None else AsyncMock()
    with (
        patch.dict("os.environ", {"DATABASE_URL": BASE_URL}),
        patch.object(audit_lambda, "create_async_engine", return_value=engine) as create_engine,
        patch.object(audit_lambda, "async_sessionmaker", return_value=factory),
        patch("app.services.audit.log_action", la),
    ):
        yield SimpleNamespace(
            session=session,
            engine=engine,
            factory=factory,
            create_engine=create_engine,
            log_action=la,
        )


# ---------------------------------------------------------------------------
# _process_message — tenant targeting + write
# ---------------------------------------------------------------------------


async def test_process_message_writes_row_to_named_tenant_db():
    """The engine URL is derived from the message's tenant_db_name and the
    audit row is written then committed against that DB."""
    body = _body(tenant_db_name="feoh_acme")
    with _harness() as h:
        await audit_lambda._process_message(body)

    # Tenant-isolation invariant: URL host/creds kept, only the db name swapped.
    h.create_engine.assert_called_once()
    assert h.create_engine.call_args.args[0] == "postgresql+asyncpg://u:p@host:5432/feoh_acme"

    h.log_action.assert_awaited_once()
    kwargs = h.log_action.await_args.kwargs
    assert kwargs["correlation_id"] == uuid.UUID(body["correlation_id"])
    assert kwargs["organization_id"] == uuid.UUID(body["organization_id"])
    assert kwargs["actor_id"] == uuid.UUID(body["actor_id"])
    assert kwargs["action"] == "payment.voided"
    assert kwargs["entity_type"] == "payment"
    assert kwargs["entity_id"] == uuid.UUID(body["entity_id"])
    assert kwargs["details"] == {"reason": "test"}
    # The session is passed positionally as the first arg.
    assert h.log_action.await_args.args[0] is h.session

    h.session.commit.assert_awaited_once()
    h.session.rollback.assert_not_awaited()
    h.engine.dispose.assert_awaited_once()


async def test_process_message_targets_the_exact_db_name_from_the_message():
    """A different tenant_db_name produces a different URL — proves the
    consumer trusts only that field for routing (the isolation chokepoint)."""
    with _harness() as h:
        await audit_lambda._process_message(_body(tenant_db_name="feoh_techflow"))
    assert h.create_engine.call_args.args[0].endswith("/feoh_techflow")


# ---------------------------------------------------------------------------
# _process_message — failure handling
# ---------------------------------------------------------------------------


async def test_process_message_rolls_back_and_reraises_on_write_failure():
    """A failed log_action must roll back, dispose the engine, and re-raise
    so SQS retries/DLQs instead of silently dropping the audit event."""
    boom = AsyncMock(side_effect=RuntimeError("db down"))
    with _harness(log_action=boom) as h:
        with pytest.raises(RuntimeError, match="db down"):
            await audit_lambda._process_message(_body())

    h.session.commit.assert_not_awaited()
    h.session.rollback.assert_awaited_once()
    h.engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _process_message — nullable actor_id (system actor)
# ---------------------------------------------------------------------------


async def test_process_message_coerces_absent_actor_id_to_none():
    """A message with no actor_id (system actor) passes actor_id=None
    rather than raising on uuid.UUID(None)."""
    with _harness() as h:
        await audit_lambda._process_message(_body(actor_id=None))
    assert h.log_action.await_args.kwargs["actor_id"] is None


async def test_process_message_coerces_present_actor_id_to_uuid():
    actor = str(uuid.uuid4())
    with _harness() as h:
        await audit_lambda._process_message(_body(actor_id=actor))
    assert h.log_action.await_args.kwargs["actor_id"] == uuid.UUID(actor)


# ---------------------------------------------------------------------------
# handler — SQS batch loop
# ---------------------------------------------------------------------------


def _handler_with_recorder(event):
    """Run handler with _process_message stubbed to record bodies, and a
    fake event loop so we don't depend on a real one in a sync test."""
    seen: list[dict] = []
    fake_loop = MagicMock(run_until_complete=MagicMock())
    with (
        patch.object(audit_lambda.asyncio, "get_event_loop", return_value=fake_loop),
        patch.object(audit_lambda, "_process_message", new=MagicMock(side_effect=seen.append)),
    ):
        result = audit_lambda.handler(event, None)
    return result, seen


def test_handler_processes_every_record_in_the_batch():
    bodies = [_body(action="a1"), _body(action="a2")]
    event = {"Records": [{"body": json.dumps(b)} for b in bodies]}
    result, seen = _handler_with_recorder(event)
    assert result == {"statusCode": 200}
    assert [b["action"] for b in seen] == ["a1", "a2"]


def test_handler_returns_200_for_empty_batch():
    result, seen = _handler_with_recorder({})
    assert result == {"statusCode": 200}
    assert seen == []
