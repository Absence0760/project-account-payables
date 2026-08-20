"""The SQS dispatchers never issue their boto3 round trip on the event loop.

`FEOH_EXTRACTION_MODE` / `FEOH_ERP_MODE` / `FEOH_AUDIT_MODE` set to `lambda`
route the work to SQS. All three `_send_to_sqs` helpers are **synchronous**
boto3: constructing the client resolves the credential chain (which can reach
the instance-metadata endpoint) and `send_message` is a full HTTPS round trip.
Each was called inline from an `async def`, so in lambda mode the loop was
occupied for that whole window and every other in-flight request on the worker
waited behind it.

The call sites matter as much as the mechanism:

* `dispatch_extraction` is awaited from the invoice upload route and from the
  PUBLIC email-intake webhook;
* `dispatch_erp` from the ERP send path;
* `dispatch_auth_audit` from `api/auth.py` — the login endpoint writes an auth
  audit row on **every** attempt, which is the most concurrent surface in the
  app and the one the project invariant calls out as `Critical`.

Same property `tests/test_storage_nonblocking.py` pins for S3: the blocking
call really does leave the loop thread, and the AST scan below stops a future
edit putting it back inline.
"""

from __future__ import annotations

import ast
import pathlib
import threading
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import settings
from app.services import audit_dispatch, erp_dispatch, extraction_dispatch

SERVICES_DIR = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"

#: The three modules that put a job on SQS, and the coroutine that reaches it.
DISPATCH_MODULES = (
    SERVICES_DIR / "extraction_dispatch.py",
    SERVICES_DIR / "erp_dispatch.py",
    SERVICES_DIR / "audit_dispatch.py",
)


def _recording_sqs_client(calls: list[int | None]) -> MagicMock:
    """A boto3 stand-in that records the thread `send_message` runs on."""

    def _note(*_args, **_kwargs):
        calls.append(threading.current_thread().ident)
        return {}

    client = MagicMock()
    client.send_message = MagicMock(side_effect=_note)
    return client


@pytest.mark.asyncio
async def test_extraction_sqs_send_runs_off_the_event_loop_thread(monkeypatch):
    loop_thread = threading.current_thread().ident
    calls: list[int | None] = []
    monkeypatch.setattr(settings, "extraction_mode", "lambda")

    with patch.object(
        extraction_dispatch.boto3, "client", lambda *a, **k: _recording_sqs_client(calls)
    ):
        await extraction_dispatch.dispatch_extraction(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert calls, "no SQS message was sent"
    assert all(tid != loop_thread for tid in calls), (
        "the extraction SQS round trip ran on the event loop thread — every "
        "upload and every inbound intake email waits behind it"
    )


@pytest.mark.asyncio
async def test_erp_sqs_send_runs_off_the_event_loop_thread(monkeypatch):
    loop_thread = threading.current_thread().ident
    calls: list[int | None] = []
    monkeypatch.setattr(settings, "erp_mode", "lambda")

    with patch.object(erp_dispatch.boto3, "client", lambda *a, **k: _recording_sqs_client(calls)):
        await erp_dispatch.dispatch_erp(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())

    assert calls, "no SQS message was sent"
    assert all(tid != loop_thread for tid in calls), (
        "the ERP SQS round trip ran on the event loop thread"
    )


@pytest.mark.asyncio
async def test_audit_sqs_send_runs_off_the_event_loop_thread(monkeypatch):
    """Both audit entry points — the tenant-session one and the auth one."""
    loop_thread = threading.current_thread().ident
    calls: list[int | None] = []
    monkeypatch.setattr(settings, "audit_mode", "lambda")
    monkeypatch.setattr(
        audit_dispatch, "_resolve_tenant_db_name", AsyncMock(return_value="feoh_acme")
    )

    with patch.object(audit_dispatch.boto3, "client", lambda *a, **k: _recording_sqs_client(calls)):
        await audit_dispatch.dispatch_audit(
            MagicMock(),
            correlation_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            action="invoice.approved",
            entity_type="invoice",
            entity_id=uuid.uuid4(),
            details={},
        )
        await audit_dispatch.dispatch_auth_audit(
            organization_id=uuid.uuid4(),
            actor_id=uuid.uuid4(),
            action="auth.login",
        )

    assert len(calls) == 2, f"expected one SQS message per entry point, got {len(calls)}"
    assert all(tid != loop_thread for tid in calls), (
        "an audit SQS round trip ran on the event loop thread — in lambda mode "
        "every login attempt reaches this call"
    )


def _blocking_calls_in_async_defs(path: pathlib.Path) -> list[str]:
    """Every `_send_to_sqs(...)` reached directly (not via to_thread) from an
    `async def` in `path`. Pure AST — no import, no execution."""
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            if isinstance(func, ast.Name) and func.id == "_send_to_sqs":
                # `asyncio.to_thread(_send_to_sqs, ...)` passes it as an
                # argument, never calls it — so a bare Call node IS the bug.
                offenders.append(f"{path.name}:{inner.lineno} in {node.name}()")
    return offenders


def test_no_dispatcher_calls_send_to_sqs_inline_from_a_coroutine():
    offenders: list[str] = []
    for path in DISPATCH_MODULES:
        offenders.extend(_blocking_calls_in_async_defs(path))

    assert not offenders, (
        "a synchronous boto3 SQS send is issued directly from a coroutine — hand "
        "it to `asyncio.to_thread(_send_to_sqs, ...)` instead: " + ", ".join(offenders)
    )
