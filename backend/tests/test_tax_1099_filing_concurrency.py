"""Real-Postgres concurrency test for 1099 e-filing idempotency (issue #166).

BUG: ``POST /api/tax/1099/file`` used check-then-act ordering — it called the
partner adapter's ``submit_batch`` (a real, consequential IRS e-file
submission) BEFORE inserting the ``(organization_id, idempotency_key)``
idempotency row. Two concurrent submits with the same key both read "no
existing filing yet", both called the partner (a duplicate IRS filing —
a real-world consequence, not just a DB inconsistency), and only THEN did
the loser's insert hit the unique constraint and 500 out — too late to
prevent the double-file.

FIX: reserve the idempotency row (``status="pending"``) and flush it BEFORE
calling the partner, mirroring the "claim the slot first" pattern already
used for money-moving writes elsewhere in this codebase (the payment run's
row-lock-then-flip-to-executing ordering in ``api/payments.py::execute_
payment_run``, proven by ``tests/test_payment_concurrency.py``, and the
claim-before-upload ordering in ``services/peppol_receive.py``). A concurrent
duplicate now hits the unique constraint immediately and gets the winner's
stored result instead of ever reaching the partner a second time. If the
partner call itself fails after the slot is claimed, the placeholder row is
deleted so a legitimate retry isn't permanently blocked.

This is a genuine concurrency bug a mocked-session unit test cannot prove —
a single MagicMock session can't model two real DB connections racing to
insert the same unique key. Like ``test_payment_concurrency.py``, this uses
the ``realdb`` fixture's per-key session makers: each call hands back an
independent engine/connection, so two coroutines contend for the real
Postgres unique index, not a mock.

Requires the dev Postgres (``pnpm db:up``); skips otherwise, like every
other ``realdb`` test.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.tax_filing import Tax1099Filing
from app.services.tax_filing_adapters.base import FilingBatchResult

pytestmark = pytest.mark.asyncio

YEAR = 2026


def _user(uid: uuid.UUID, role: str = "ap_manager"):
    return SimpleNamespace(id=uid, full_name="Concurrency Tester", roles=[role])


def _org(org_id: uuid.UUID):
    return SimpleNamespace(id=org_id, name="PyTest", slug="pytesta", settings={})


async def test_concurrent_1099_file_calls_partner_exactly_once(realdb):
    """Two concurrent /1099/file submits with the SAME idempotency key must
    result in the partner adapter's submit_batch being called exactly ONCE.
    The loser gets the winner's stored (idempotent) result — never a second
    partner call, never a raw 500.
    """
    from app.api.tax import FileBatchRequest, file_1099_batch

    info = realdb.info("a")
    org_id = info.org_id
    admin_id = info.users["admin"]

    call_count = 0

    async def _counting_submit_batch(*, tax_year, forms, idempotency_key):
        nonlocal call_count
        call_count += 1
        # Yield control so both racers are genuinely in flight — the DB's
        # unique-constraint contention, not timing, is what serializes them.
        await asyncio.sleep(0)
        return FilingBatchResult(
            status="accepted",
            provider="mock",
            confirmation_number=f"CONF-{call_count}",
            tax_year=tax_year,
            submitted_count=len(forms),
            accepted_count=len(forms),
            rejected_count=0,
            forms=[],
        )

    adapter = SimpleNamespace(provider_name="mock", submit_batch=_counting_submit_batch)

    body = FileBatchRequest(year=YEAR, idempotency_key="race-key-166")

    async def _file_once():
        session_mk = realdb.sessionmaker("a")
        async with session_mk() as db:
            try:
                res = await file_1099_batch(
                    body=body,
                    db=db,
                    org=_org(org_id),
                    user=_user(admin_id),
                    org_id=org_id,
                )
                return ("ok", res)
            except HTTPException as exc:
                return ("http", exc.status_code)

    with patch("app.api.tax.get_tax_filing_adapter", return_value=adapter):
        results = await asyncio.gather(_file_once(), _file_once())

    assert call_count == 1, f"adapter.submit_batch called {call_count}x (double-filed!)"

    # Neither racer should ever see a raw 500 — the loser gets a clean
    # idempotent response (or, in the vanishingly rare case its own re-query
    # races again, an explicit 409 — but never an unhandled DB error).
    kinds = [kind for kind, _ in results]
    assert all(k in ("ok", "http") for k in kinds), results
    http_results = [r for kind, r in results if kind == "http"]
    assert all(code == 409 for code in http_results), (
        f"expected only clean 409s on the http path, got {http_results}"
    )

    oks = [r for kind, r in results if kind == "ok"]
    already_filed_flags = sorted(r["already_filed"] for r in oks)
    # Exactly one racer actually filed (already_filed=False); any other racer
    # that didn't 409 got the winner's stored, idempotent result.
    assert already_filed_flags.count(False) == 1, results
    confirmations = {r["confirmation_number"] for r in oks}
    assert len(confirmations) == 1, f"racers disagree on confirmation: {results}"

    # Exactly one filing row persisted for the key, in its final (non-pending)
    # state — no dangling reservation left behind.
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(Tax1099Filing).where(Tax1099Filing.idempotency_key == "race-key-166")
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1, f"expected exactly 1 filing row, got {len(rows)}"
        assert rows[0].status == "accepted"
