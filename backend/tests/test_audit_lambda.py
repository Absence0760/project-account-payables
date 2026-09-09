"""Tests for the SQS-triggered audit Lambda handler (audit_mode = "lambda").

DB-free: the consumer builds its own engine/session from the message and
calls ``audit.log_action``, so we mock ``create_async_engine``,
``async_sessionmaker`` and ``log_action`` to assert the orchestration —
tenant-DB targeting, UUID coercion, commit/rollback discipline, and the
SQS batch loop — without a live Postgres.

Guards two project invariants that are otherwise unverified in lambda
mode:
  * tenant isolation — the engine is built outside ``get_tenant_db``, so we
    lock that its URL comes from the ``Organization.db_name`` column this
    handler RESOLVES from ``organization_id``, and that the ``tenant_db_name``
    the producer still puts on the message body steers nothing. It used to be
    read straight off the body: the one place in the codebase where a tenant
    database name was taken from an input rather than re-derived from a
    resolved row at the point of use.
  * append-only audit trail — a well-formed message must produce one
    committed ``log_action`` write, and a failed write must roll back AND
    re-raise so SQS retries/DLQs rather than silently dropping the event. An
    unresolvable organization raises for the same reason: this message is the
    only copy of the event.
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
        # The producer still sends this; the handler must ignore it. Seeded with a
        # value that is NOT the org's real DB so a regression to trusting it is a
        # failure, not a coincidence.
        "tenant_db_name": "feoh_attacker",
    }
    body.update(overrides)
    return body


class _FakeSession:
    """Async-context-manager session that records commit/rollback.

    ``exec_result`` is what ``execute`` returns — the control session uses it to
    hand back the resolved ``Organization.db_name``; the tenant session never
    executes anything itself (``log_action`` is patched).
    """

    def __init__(self, exec_result=None) -> None:
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self._exec_result = exec_result

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def execute(self, *_a, **_k):
        return self._exec_result


def _scalar(value):
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=value)
    return r


@contextmanager
def _harness(log_action: AsyncMock | None = None, *, resolved_db_name: str | None = "feoh_acme"):
    """Patch the module's engine/session factories + audit.log_action.

    Two engines now, in order: the control-plane one the handler opens to resolve
    ``Organization.db_name``, then the tenant one it builds from that name.
    """
    control_engine = MagicMock(dispose=AsyncMock())
    tenant_engine = MagicMock(dispose=AsyncMock())
    ctrl_session = _FakeSession(_scalar(resolved_db_name))
    session = _FakeSession()
    la = log_action if log_action is not None else AsyncMock()
    with (
        patch.dict("os.environ", {"DATABASE_URL": BASE_URL}),
        patch.object(
            audit_lambda,
            "create_async_engine",
            MagicMock(side_effect=[control_engine, tenant_engine]),
        ) as create_engine,
        patch.object(
            audit_lambda,
            "async_sessionmaker",
            MagicMock(
                side_effect=[
                    MagicMock(return_value=ctrl_session),
                    MagicMock(return_value=session),
                ]
            ),
        ),
        patch("app.services.audit.log_action", la),
    ):
        yield SimpleNamespace(
            session=session,
            ctrl_session=ctrl_session,
            engine=tenant_engine,
            control_engine=control_engine,
            create_engine=create_engine,
            log_action=la,
        )


# ---------------------------------------------------------------------------
# _process_message — tenant targeting + write
# ---------------------------------------------------------------------------


async def test_process_message_writes_row_to_resolved_tenant_db():
    """The engine URL is derived from the RESOLVED Organization.db_name and the
    audit row is written then committed against that DB."""
    body = _body()
    with _harness(resolved_db_name="feoh_acme") as h:
        await audit_lambda._process_message(body)

    # Two engines: control plane first (to resolve the org), tenant second.
    assert h.create_engine.call_count == 2
    assert h.create_engine.call_args_list[0].args[0] == BASE_URL
    # Tenant-isolation invariant: URL host/creds kept, only the db name swapped.
    assert (
        h.create_engine.call_args_list[1].args[0] == "postgresql+asyncpg://u:p@host:5432/feoh_acme"
    )
    # The control engine is released as soon as the lookup is done.
    h.control_engine.dispose.assert_awaited_once()

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


async def test_process_message_targets_the_resolved_db_name():
    """A different resolved db_name produces a different URL — the routing input
    is the control-plane column, which is the isolation chokepoint."""
    with _harness(resolved_db_name="feoh_techflow") as h:
        await audit_lambda._process_message(_body())
    assert h.create_engine.call_args_list[1].args[0].endswith("/feoh_techflow")


async def test_the_tenant_db_name_on_the_message_body_is_ignored():
    """The producer still sends `tenant_db_name`; nothing here reads it.

    This is the whole point of the change: an SQS body is an input, and a tenant
    database name taken from an input is one the `get_tenant` org-claim
    cross-check never sees. Even a body naming a real sibling tenant must not
    steer the connection.
    """
    with _harness(resolved_db_name="feoh_acme") as h:
        await audit_lambda._process_message(_body(tenant_db_name="feoh_techflow"))
    urls = [c.args[0] for c in h.create_engine.call_args_list]
    assert urls[1].endswith("/feoh_acme")
    assert not any("feoh_techflow" in u for u in urls)


async def test_a_message_with_no_tenant_db_name_still_routes():
    """Corollary — the field is dead weight, so its absence changes nothing."""
    body = _body()
    del body["tenant_db_name"]
    with _harness(resolved_db_name="feoh_acme") as h:
        await audit_lambda._process_message(body)
    assert h.create_engine.call_args_list[1].args[0].endswith("/feoh_acme")
    h.log_action.assert_awaited_once()


async def test_unknown_organization_raises_rather_than_dropping_the_row():
    """No resolvable org → raise, so SQS redelivers and ultimately dead-letters.

    The extraction/ERP handlers return silently here; this one must not. Their
    message asks for work that can be re-requested — this message IS the audit
    event, and the trail is append-only, so a silent return destroys evidence.
    """
    with _harness(resolved_db_name=None) as h:
        with pytest.raises(ValueError, match="not found"):
            await audit_lambda._process_message(_body())
    # No tenant engine was ever built, and nothing was written.
    assert h.create_engine.call_count == 1
    h.control_engine.dispose.assert_awaited_once()
    h.log_action.assert_not_awaited()


async def test_a_missing_organization_id_raises_before_any_engine():
    """`organization_id` is the routing key now — a body without one must fail
    before a connection is opened, not against a malformed URL."""
    body = _body()
    del body["organization_id"]
    create_engine = MagicMock()
    with (
        patch.dict("os.environ", {"DATABASE_URL": BASE_URL}),
        patch.object(audit_lambda, "create_async_engine", create_engine),
    ):
        with pytest.raises(KeyError):
            await audit_lambda._process_message(body)
    create_engine.assert_not_called()


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
    h.control_engine.dispose.assert_awaited_once()


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
