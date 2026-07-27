"""Tests for the SQS-triggered extraction Lambda (extraction_mode = "lambda").

DB-free: the consumer builds a control engine to resolve the tenant DB name,
then a tenant engine to load the invoice and run extraction. We mock both
engines/sessions to assert the orchestration the existing suite never reaches:

  * the tenant engine targets ``org.db_name`` (so a message for org A never
    runs against org B / the control plane) — tenant-isolation-adjacent
  * org-not-found and invoice-not-found short-circuits dispose engines and
    never call run_extraction
  * a run_extraction failure rolls back AND re-raises (so SQS redelivers),
    with both engines disposed in finally
  * the handler SQS batch loop parses each record and returns statusCode 200
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import extraction_lambda

BASE_URL = "postgresql+asyncpg://u:p@host:5432/feohledger"


def _body(**overrides) -> dict:
    body = {
        "invoice_id": str(uuid.uuid4()),
        "org_id": str(uuid.uuid4()),
        "actor_id": str(uuid.uuid4()),
    }
    body.update(overrides)
    return body


def _result(scalar):
    r = MagicMock()
    r.scalar_one_or_none = MagicMock(return_value=scalar)
    return r


class _FakeSession:
    def __init__(self, exec_result) -> None:
        self._exec_result = exec_result
        self.rollback = AsyncMock()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_args) -> bool:
        return False

    async def execute(self, *_a, **_k):
        return self._exec_result


@contextmanager
def _harness(*, org, invoice, run_extraction: AsyncMock | None = None):
    control_engine = MagicMock(dispose=AsyncMock())
    tenant_engine = MagicMock(dispose=AsyncMock())
    ctrl_session = _FakeSession(_result(org))
    tenant_session = _FakeSession(_result(invoice))
    run = run_extraction if run_extraction is not None else AsyncMock()
    with (
        patch.dict("os.environ", {"DATABASE_URL": BASE_URL}),
        patch.object(
            extraction_lambda,
            "create_async_engine",
            MagicMock(side_effect=[control_engine, tenant_engine]),
        ) as create_engine,
        patch.object(
            extraction_lambda,
            "async_sessionmaker",
            MagicMock(
                side_effect=[
                    MagicMock(return_value=ctrl_session),
                    MagicMock(return_value=tenant_session),
                ]
            ),
        ),
        patch("app.services.extraction.run_extraction", run),
    ):
        yield SimpleNamespace(
            create_engine=create_engine,
            control_engine=control_engine,
            tenant_engine=tenant_engine,
            run_extraction=run,
        )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


async def test_process_message_runs_extraction_for_resolved_tenant():
    body = _body()
    invoice = SimpleNamespace(id=uuid.UUID(body["invoice_id"]))
    org = SimpleNamespace(id=uuid.UUID(body["org_id"]), db_name="feoh_acme")
    with _harness(org=org, invoice=invoice) as h:
        await extraction_lambda._process_message(body)

    h.run_extraction.assert_awaited_once()
    assert h.run_extraction.await_args.args[1] is invoice
    assert h.run_extraction.await_args.kwargs["actor_id"] == uuid.UUID(body["actor_id"])
    h.control_engine.dispose.assert_awaited_once()
    h.tenant_engine.dispose.assert_awaited_once()


async def test_process_message_connects_to_org_db_name_not_control():
    body = _body()
    invoice = SimpleNamespace(id=uuid.UUID(body["invoice_id"]))
    org = SimpleNamespace(id=uuid.UUID(body["org_id"]), db_name="feoh_acme")
    with _harness(org=org, invoice=invoice) as h:
        await extraction_lambda._process_message(body)
    # Second engine is the tenant one; URL swaps only the db name.
    assert (
        h.create_engine.call_args_list[1].args[0] == "postgresql+asyncpg://u:p@host:5432/feoh_acme"
    )


# ---------------------------------------------------------------------------
# short-circuits
# ---------------------------------------------------------------------------


async def test_process_message_org_not_found_disposes_and_returns():
    with _harness(org=None, invoice=None) as h:
        await extraction_lambda._process_message(_body())
    h.control_engine.dispose.assert_awaited_once()
    # No tenant engine was ever created (only the control engine was consumed).
    h.tenant_engine.dispose.assert_not_awaited()
    h.run_extraction.assert_not_awaited()
    assert h.create_engine.call_count == 1


async def test_process_message_invoice_not_found_disposes_both_engines():
    org = SimpleNamespace(id=uuid.uuid4(), db_name="feoh_acme")
    with _harness(org=org, invoice=None) as h:
        await extraction_lambda._process_message(_body())
    h.run_extraction.assert_not_awaited()
    h.control_engine.dispose.assert_awaited_once()
    h.tenant_engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# failure / redelivery
# ---------------------------------------------------------------------------


async def test_process_message_rolls_back_and_reraises_on_extraction_error():
    org = SimpleNamespace(id=uuid.uuid4(), db_name="feoh_acme")
    invoice = SimpleNamespace(id=uuid.uuid4())
    boom = AsyncMock(side_effect=RuntimeError("extract failed"))
    with _harness(org=org, invoice=invoice, run_extraction=boom) as h:
        with pytest.raises(RuntimeError, match="extract failed"):
            await extraction_lambda._process_message(_body())
    h.control_engine.dispose.assert_awaited_once()
    h.tenant_engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# handler batch loop
# ---------------------------------------------------------------------------


def test_handler_processes_each_record_and_returns_200():
    bodies = [_body(), _body()]
    event = {"Records": [{"body": json.dumps(b)} for b in bodies]}
    seen: list[dict] = []
    fake_loop = MagicMock(run_until_complete=MagicMock())
    with (
        patch.object(extraction_lambda.asyncio, "get_event_loop", return_value=fake_loop),
        patch.object(extraction_lambda, "_process_message", new=MagicMock(side_effect=seen.append)),
    ):
        result = extraction_lambda.handler(event, None)
    assert result == {"statusCode": 200}
    assert [b["invoice_id"] for b in seen] == [b["invoice_id"] for b in bodies]
