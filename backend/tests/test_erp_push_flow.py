"""ERP push pipeline — `send_to_erp`, `send_to_erp_internal`, and
`retry_erp`.

`send_to_erp` is the load-bearing path that moves an approved invoice
to `done` via the ERP. A regression in retry counting double-charges
ERP IDs; a regression in the "all retries exhausted" branch wedges
invoices in `sending_to_erp` forever; a regression in the happy-path
transition chain skips audit rows the SOC 2 auditor wants.

These tests pin:
  - happy path walks approved → sending_to_erp → sent_to_erp → done
    with audit rows on each leg
  - the erp_reference returned by the adapter rides on the
    `invoice.erp_confirmed` audit row's details
  - workflow_instance.state_data captures `erp_retries` and
    `erp_reference` after success
  - all three retries failing transitions the invoice to `failed`
    (NOT done), persists `erp_retries=3` and `last_error` on the
    instance, and marks `instance.state="failed"`
  - retry_erp refuses to retry an invoice that was never approved
    (the `approved_by` guard) — money invariant: we don't push to
    ERP unless an actual human approved
  - retry_erp resets erp_retries to 0 and parks the invoice at
    sending_to_erp WITHOUT running the ERP call inline — the route's
    dispatch_erp owns the call (org config + FEOH_ERP_MODE); an inline
    call would double-post and always use the mock adapter

Adapter calls and `asyncio.sleep` are stubbed — we never want real
network latency or real backoff sleeps inside a unit test.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.models.invoice import InvoiceStatus
from app.services.erp import retry_erp, send_to_erp

_UNSET = object()


def _invoice(*, status=InvoiceStatus.approved, approved_by=_UNSET):
    """Invoice fixture. `approved_by` defaults to a fresh UUID (the
    common case); pass `approved_by=None` explicitly to model an
    invoice that was never approved."""
    if approved_by is _UNSET:
        approved_by = uuid.uuid4()
    return SimpleNamespace(
        id=uuid.uuid4(),
        status=status,
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        approved_by=approved_by,
        invoice_number="INV-1",
        vendor_name="Acme",
        amount=Decimal("100.00"),
        currency="USD",
        vendor_tax_id=None,
        invoice_date=None,
        due_date=None,
        po_number=None,
        description=None,
        subtotal=None,
        tax_amount=None,
        tax_rate=None,
        discount_amount=None,
        shipping_amount=None,
        gl_account=None,
        cost_center=None,
        payment_terms=None,
        payment_method=None,
        bill_to_address=None,
        remit_to_address=None,
        vendor_address=None,
    )


def _instance(*, state_data=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        state="active",
        state_data=state_data,
        correlation_id=uuid.uuid4(),
    )


class _AuditRecorder:
    def __init__(self):
        self.rows: list[dict] = []

    async def __call__(self, db, **kwargs):
        self.rows.append(kwargs)

    def actions(self) -> list[str]:
        return [r["action"] for r in self.rows]

    def transitions(self) -> list[tuple[str, str]]:
        return [
            (r["details"]["old_status"], r["details"]["new_status"])
            for r in self.rows
            if "old_status" in (r.get("details") or {})
        ]


# ---------------------------------------------------------------------------
# send_to_erp — happy path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_erp_happy_path_walks_approved_to_done():
    """ERP call succeeds on the first try → invoice walks
    approved → sending_to_erp → sent_to_erp → done. Three audit
    rows; the erp_reference rides on the second."""
    inv = _invoice()
    inst = _instance()
    recorder = _AuditRecorder()
    db = AsyncMock()

    with (
        patch("app.services.workflow_engine.dispatch_audit", new=recorder),
        patch("app.services.erp._call_erp", AsyncMock(return_value="ERP-12345")),
        patch("app.services.erp.get_workflow_instance", AsyncMock(return_value=inst)),
        patch("app.services.erp.complete_workflow", AsyncMock()),
    ):
        await send_to_erp(db, inv)

    assert inv.status == InvoiceStatus.done
    # Three transitions in order:
    assert recorder.transitions() == [
        ("approved", "sending_to_erp"),
        ("sending_to_erp", "sent_to_erp"),
        ("sent_to_erp", "done"),
    ]
    # erp_reference rides on the sent_to_erp row.
    confirm = next(r for r in recorder.rows if r["action"] == "invoice.erp_confirmed")
    assert confirm["details"]["erp_reference"] == "ERP-12345"

    # Instance state_data captures the retry count and the ERP ref.
    assert inst.state_data["erp_reference"] == "ERP-12345"
    assert inst.state_data["erp_retries"] == 1


# ---------------------------------------------------------------------------
# send_to_erp — retry exhaustion.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_erp_retry_exhausted_transitions_to_failed():
    """All MAX_RETRIES attempts raise → invoice ends up `failed`,
    not `done`. The instance carries the final retry count + the
    last_error string. asyncio.sleep is stubbed so the backoff
    delays don't run."""
    inv = _invoice()
    inst = _instance()
    recorder = _AuditRecorder()
    db = AsyncMock()

    with (
        patch("app.services.workflow_engine.dispatch_audit", new=recorder),
        patch(
            "app.services.erp._call_erp",
            AsyncMock(side_effect=RuntimeError("connection refused")),
        ),
        patch("app.services.erp.get_workflow_instance", AsyncMock(return_value=inst)),
        patch("app.services.erp.asyncio.sleep", AsyncMock()),
    ):
        await send_to_erp(db, inv)

    assert inv.status == InvoiceStatus.failed
    # Transitions: approved → sending_to_erp, then sending_to_erp → failed.
    assert recorder.transitions()[0] == ("approved", "sending_to_erp")
    assert recorder.transitions()[-1] == ("sending_to_erp", "failed")

    # Final audit row carries the error string and retry count.
    fail_row = next(r for r in recorder.rows if r["action"] == "invoice.erp_failed")
    assert fail_row["details"]["error"] == "connection refused"
    assert fail_row["details"]["retries"] == 3

    # Instance reflects failure for the queue-builder.
    assert inst.state == "failed"
    assert inst.state_data["erp_retries"] == 3
    assert inst.state_data["last_error"] == "connection refused"


@pytest.mark.asyncio
async def test_send_to_erp_retry_attempts_use_exponential_backoff():
    """The retry loop must use exponential backoff (2, 4, ... seconds)
    so a transient outage doesn't get hammered. We capture the sleep
    durations and assert the doubling pattern. The final attempt
    doesn't sleep (no retry after it)."""
    inv = _invoice()
    inst = _instance()
    sleep_mock = AsyncMock()
    db = AsyncMock()

    with (
        patch("app.services.workflow_engine.dispatch_audit", new=_AuditRecorder()),
        patch(
            "app.services.erp._call_erp",
            AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch("app.services.erp.get_workflow_instance", AsyncMock(return_value=inst)),
        patch("app.services.erp.asyncio.sleep", sleep_mock),
    ):
        await send_to_erp(db, inv)

    # 3 attempts → 2 sleeps (no sleep after the last attempt).
    delays = [c.args[0] for c in sleep_mock.call_args_list]
    assert delays == [2, 4], f"expected 2s, 4s backoff, got {delays}"


# ---------------------------------------------------------------------------
# send_to_erp — resumes from persisted retry count.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_erp_resumes_from_persisted_retry_count():
    """Instance already shows `erp_retries=2` from a prior partial
    failure. The next entry into send_to_erp starts at attempt 2,
    leaving only one slot. If THIS attempt also fails, the invoice
    goes to `failed` immediately — no extra retries."""
    inv = _invoice()
    inst = _instance(state_data={"erp_retries": 2})
    recorder = _AuditRecorder()
    db = AsyncMock()

    sleep_mock = AsyncMock()
    with (
        patch("app.services.workflow_engine.dispatch_audit", new=recorder),
        patch(
            "app.services.erp._call_erp",
            AsyncMock(side_effect=RuntimeError("still down")),
        ),
        patch("app.services.erp.get_workflow_instance", AsyncMock(return_value=inst)),
        patch("app.services.erp.asyncio.sleep", sleep_mock),
    ):
        await send_to_erp(db, inv)

    assert inv.status == InvoiceStatus.failed
    # No sleep — there was only one slot left, and after it failed
    # we exhausted retries immediately.
    assert sleep_mock.call_count == 0


# ---------------------------------------------------------------------------
# retry_erp — guard rails.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_erp_refuses_invoice_that_was_never_approved():
    """`approved_by` is None → 409. The ERP receives nothing that
    wasn't human-approved. Without this guard, an automated retry
    job could drive any failed invoice into the ERP. The money
    invariant: money never moves on an un-approved invoice."""
    from fastapi import HTTPException

    inv = _invoice(approved_by=None, status=InvoiceStatus.failed)
    db = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await retry_erp(db, inv)

    assert exc.value.status_code == 409
    assert "never approved" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_retry_erp_prepares_state_but_never_runs_the_erp_call_inline():
    """A human pressing "retry" should get a full fresh budget, not
    the leftover slots from the prior failure. Verify by setting
    `erp_retries=3` (exhausted) on the instance and confirming the
    retry resets it to 0 and parks the invoice at sending_to_erp.

    Regression: retry_erp used to ALSO run send_to_erp_internal inline
    — without the org's erp_config (so the retry always posted via the
    MOCK adapter), racing the route's own dispatch_erp (double-post),
    and bypassing FEOH_ERP_MODE=lambda. The actual call is the
    dispatcher's job; retry_erp must only prepare state."""
    inv = _invoice(status=InvoiceStatus.failed)
    inst = _instance(state_data={"erp_retries": 3, "last_error": "old"})
    db = AsyncMock()
    internal_mock = AsyncMock()
    recorder = _AuditRecorder()

    with (
        patch("app.services.workflow_engine.dispatch_audit", new=recorder),
        patch("app.services.erp.get_workflow_instance", AsyncMock(return_value=inst)),
        patch("app.services.erp.send_to_erp_internal", internal_mock),
    ):
        await retry_erp(db, inv)

    # The retry counter was reset and the instance reactivated.
    assert inst.state_data["erp_retries"] == 0
    assert inst.state == "active"
    # Invoice parked at sending_to_erp; dispatch_erp (the route's next
    # call) performs the actual ERP post with the org's config.
    assert inv.status == InvoiceStatus.sending_to_erp
    internal_mock.assert_not_awaited()
    # Audit row marks this as a retry, not a fresh submission.
    assert any(r["action"] == "invoice.erp_retried" for r in recorder.rows)


# ---------------------------------------------------------------------------
# send_to_erp_internal — org ERP config plumbing.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_to_erp_internal_passes_org_erp_config_to_call_erp():
    """Regression: send_to_erp_internal used to drop its erp_config on the
    floor (`_call_erp(invoice)`), so the local dispatch worker — which
    resolves the org's settings.erp and passes it in — always posted via
    the MOCK adapter no matter what ERP the tenant configured. Lock the
    pass-through."""
    from app.services.erp import send_to_erp_internal

    inv = _invoice(status=InvoiceStatus.sending_to_erp)
    inst = _instance()
    call_erp = AsyncMock(return_value="ERP-REF-1")
    cfg = {"type": "netsuite", "integration_method": "direct", "account_id": "ACCT"}

    with (
        patch("app.services.workflow_engine.dispatch_audit", new=_AuditRecorder()),
        patch("app.services.erp._call_erp", call_erp),
        patch("app.services.erp.get_workflow_instance", AsyncMock(return_value=inst)),
        patch("app.services.erp.complete_workflow", AsyncMock()),
    ):
        await send_to_erp_internal(AsyncMock(), inv, erp_config=cfg)

    call_erp.assert_awaited_once_with(inv, cfg)


@pytest.mark.asyncio
async def test_call_erp_dispatches_via_configured_adapter_not_mock():
    """With a merge_dev config, _call_erp must post through the Merge.dev
    adapter (the returned reference is the Merge model id), not the mock."""
    import httpx as _httpx  # noqa: F401 — ensure module import for patch target

    from app.services.erp import _call_erp

    resp = AsyncMock()
    resp.status_code = 201
    resp.json = lambda: {"model": {"id": "merge-inv-77", "number": "INV-1"}}

    with patch("httpx.AsyncClient") as cm:
        client = cm.return_value.__aenter__.return_value
        client.post = AsyncMock(return_value=resp)
        ref = await _call_erp(
            _invoice(),
            {"integration_method": "merge_dev", "api_key": "k", "account_token": "t"},
        )

    assert ref == "merge-inv-77"
    posted_url = client.post.await_args.args[0]
    assert posted_url.endswith("/invoices")
