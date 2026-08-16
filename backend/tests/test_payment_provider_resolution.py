"""An unsupported `settings.payments.provider` must refuse, never become `mock`.

`get_payment_adapter` used to fall back to the mock adapter for ANY provider
name it had no adapter for, on the reasoning that a misconfigured org
shouldn't 500 the payments domain. `mock` is not an inert stub, though:

  - `create_payment` returns `success=True, status=completed` immediately, so
    one typo in an admin-entered settings value (`modern-treasury` for
    `modern_treasury`) made every payment in every run report as settled while
    no money moved, and flipped the invoices to `paid`;
  - `parse_webhook` verifies no signature at all, so the same typo routed the
    public webhook route to an unverified parser — under a name the
    `provider == "mock"` early-return there can't catch;
  - `void_payment` returns True unconditionally, so an upstream void that
    never happened was recorded as `voided_upstream`.

These tests pin the fail-closed behaviour at the dispatcher AND at each caller
that has to decide what to do with the refusal. The webhook half lives in
`test_payment_webhook_security.py` beside the other public-route rejections.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun

# The exact shape of a typo an admin actually makes: right processor, wrong
# separator. Never registered, so it exercises the fail-closed path.
BAD_PROVIDER = "modern-treasury"


def _user(uid: uuid.UUID):
    return SimpleNamespace(id=uid, full_name="Tester", roles=["admin"])


def _org(org_id: uuid.UUID, *, provider: str = BAD_PROVIDER):
    return SimpleNamespace(
        id=org_id,
        name="PyTest",
        slug="pytesta",
        settings={"payments": {"provider": provider, "require_run_segregation": False}},
    )


async def _seed_draft_run(mk, org_id, *, initiated_by) -> tuple[uuid.UUID, uuid.UUID]:
    """A `draft` run with one `pending` payment. Returns (run_id, payment_id)."""
    inv_id = uuid.uuid4()
    corr = uuid.uuid4()
    run_id = uuid.uuid4()
    pay_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
                vendor_name="Acme Corp",
                amount=Decimal("100.00"),
                currency="USD",
                status=InvoiceStatus.approved,
                organization_id=org_id,
                correlation_id=corr,
            )
        )
        s.add(
            PaymentRun(
                id=run_id,
                organization_id=org_id,
                status="draft",
                total_amount=Decimal("100.00"),
                initiated_by=initiated_by,
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=pay_id,
                invoice_id=inv_id,
                payment_run_id=run_id,
                amount=Decimal("100.00"),
                method="ach",
                status="pending",
                correlation_id=corr,
            )
        )
        await s.commit()
    return run_id, pay_id


# ── Run execution ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_refuses_unsupported_provider_and_leaves_run_draft(realdb):
    """The defect this closes: the run reported `completed`, every payment
    reported `completed`, and no money moved.

    The refusal has to land BEFORE the run is claimed, or the run would sit
    `executing` behind a 500 and need `/resume` to recover from a settings
    typo. `draft` is the state an operator can simply re-execute once the
    provider name is fixed.
    """
    from app.api.payments import execute_payment_run

    info = realdb.info("a")
    org_id = info.org_id
    mk = realdb.sessionmaker("a")
    run_id, pay_id = await _seed_draft_run(mk, org_id, initiated_by=info.users["ap_manager"])

    async with realdb.sessionmaker("a")() as db:
        with pytest.raises(HTTPException) as ei:
            await execute_payment_run(
                run_id=run_id,
                db=db,
                org=_org(org_id),
                user=_user(info.users["admin"]),
                entity_id=None,
            )

    assert ei.value.status_code == 409
    assert BAD_PROVIDER in ei.value.detail

    async with mk() as s:
        run = (await s.execute(select(PaymentRun).where(PaymentRun.id == run_id))).scalar_one()
        payment = (await s.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()
    assert run.status == "draft"
    assert payment.status == "pending"
    # The invariant that actually matters: nothing was reported as settled.
    assert payment.provider_payment_id is None


@pytest.mark.asyncio
async def test_execute_still_works_for_a_supported_provider(realdb):
    """The fail-closed guard must not break the ordinary path — an org with a
    registered provider (including the local-first `mock` default) executes
    exactly as before."""
    from app.api.payments import execute_payment_run

    info = realdb.info("a")
    org_id = info.org_id
    mk = realdb.sessionmaker("a")
    run_id, _ = await _seed_draft_run(mk, org_id, initiated_by=info.users["ap_manager"])

    async with realdb.sessionmaker("a")() as db:
        with (
            patch("app.api.payments.transition_invoice", new_callable=AsyncMock),
            patch("app.services.payment_erp_sync.dispatch_payment_sync", new_callable=AsyncMock),
        ):
            res = await execute_payment_run(
                run_id=run_id,
                db=db,
                org=_org(org_id, provider="mock"),
                user=_user(info.users["admin"]),
                entity_id=None,
            )

    assert res["status"] in ("completed", "submitted", "partial")


@pytest.mark.asyncio
async def test_resume_refuses_unsupported_provider(realdb):
    """`/resume` dispatches real payments exactly like `/execute`, so it takes
    the same pre-flight — otherwise the typo'd org has a second door in."""
    from app.api.payments import resume_payment_run

    info = realdb.info("a")
    org_id = info.org_id
    mk = realdb.sessionmaker("a")
    run_id, pay_id = await _seed_draft_run(mk, org_id, initiated_by=info.users["ap_manager"])
    async with mk() as s:
        run = (await s.execute(select(PaymentRun).where(PaymentRun.id == run_id))).scalar_one()
        run.status = "executing"  # the state /resume exists for
        await s.commit()

    async with realdb.sessionmaker("a")() as db:
        with pytest.raises(HTTPException) as ei:
            await resume_payment_run(
                run_id=run_id,
                db=db,
                org=_org(org_id),
                user=_user(info.users["admin"]),
                entity_id=None,
            )

    assert ei.value.status_code == 409
    async with mk() as s:
        payment = (await s.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()
    assert payment.status == "pending"


# ── Void ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_void_records_provider_not_supported_and_still_voids_locally(realdb):
    """Void is deliberately best-effort on the rail — the accounting books
    should reflect intent even when the processor can't be reached — so an
    unsupported provider must NOT block the local void.

    What it must not do is what it used to: call `mock.void_payment` (which
    returns True unconditionally) and write `voided_upstream` onto the audit
    row for a rail nobody ever asked.
    """
    from app.api.payments import VoidPaymentRequest, void_payment
    from app.models.workflow import AuditLog

    info = realdb.info("a")
    org_id = info.org_id
    mk = realdb.sessionmaker("a")
    run_id, pay_id = await _seed_draft_run(mk, org_id, initiated_by=info.users["ap_manager"])
    async with mk() as s:
        payment = (await s.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()
        payment.status = "submitted"
        payment.provider = "modern_treasury"
        payment.provider_payment_id = "px_from_the_old_rail"
        await s.commit()

    async with realdb.sessionmaker("a")() as db:
        with patch("app.api.payments.transition_invoice", new_callable=AsyncMock):
            await void_payment(
                payment_id=pay_id,
                body=VoidPaymentRequest(reason="settings typo cleanup"),
                db=db,
                org=_org(org_id),
                user=_user(info.users["admin"]),
                entity_id=None,
            )

    async with mk() as s:
        voided = (await s.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "payment.voided")))
            .scalars()
            .all()
        )
    assert voided.status == "voided"
    outcomes = [(r.details or {}).get("adapter_outcome") for r in rows]
    assert "provider_not_supported" in outcomes
    assert "voided_upstream" not in outcomes
    _ = run_id


# ── Reconciler sweep ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reconciler_counts_unsupported_provider_as_a_tenant_failure():
    """A tenant the sweep cannot poll must show up as a FAILURE, not as a
    clean pass with zero work.

    Swallowing it would leave the tenant's stuck payments un-polled and the
    sweep looking healthy on `GET /api/health/sweeps` — the exact blindness
    `services/sweep_health` exists to remove.
    """
    from app.services import payment_reconciler

    org = SimpleNamespace(
        id=uuid.uuid4(),
        db_name="feoh_pytest_never_opened",
        settings={"payments": {"provider": BAD_PROVIDER}},
    )
    result = MagicMock()
    result.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[org])))
    ctrl = AsyncMock()
    ctrl.execute = AsyncMock(return_value=result)
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=ctrl)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(payment_reconciler, "control_session_factory", factory),
        # If the guard regressed, resolution would succeed and the sweep would
        # try to open a tenant DB that does not exist — fail loudly instead.
        patch.object(payment_reconciler, "create_async_engine") as mk_engine,
    ):
        outcome = await payment_reconciler.reconcile_once()

    assert outcome.tenants_scanned == 1
    assert outcome.failures == 1
    assert outcome.payments_polled == 0
    mk_engine.assert_not_called()


# ── Corridor auction ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_corridor_auction_skips_an_unsupported_provider_without_dying():
    """One bad name in a multi-provider list must not take down the auction:
    the org's other rails can still quote, and the bad one can never win."""
    from app.services.corridor_quotes import compare_quotes
    from app.services.payment_adapters import PaymentPayload

    payload = PaymentPayload(
        invoice_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        invoice_number="INV-1001",
        amount=Decimal("1000.00"),
        currency="USD",
        method="ach",
        vendor_name="Acme Corp",
    )
    settings = {"payments": {"providers": [{"provider": BAD_PROVIDER}, {"provider": "mock"}]}}

    ranking = await compare_quotes(payload, settings)

    assert ranking.winner.provider == "mock"
    assert ranking.winner.available is True
    losers = {q.provider: q for q in ranking.runners_up}
    assert losers[BAD_PROVIDER].available is False
    assert losers[BAD_PROVIDER].unavailable_reason == "provider_not_supported"


@pytest.mark.asyncio
async def test_corridor_auction_reports_no_eligible_corridor_when_only_provider_is_unsupported():
    from app.services.corridor_quotes import NoEligibleCorridorError, compare_quotes
    from app.services.payment_adapters import PaymentPayload

    payload = PaymentPayload(
        invoice_id=str(uuid.uuid4()),
        correlation_id=str(uuid.uuid4()),
        invoice_number="INV-1002",
        amount=Decimal("50.00"),
        currency="USD",
        method="ach",
        vendor_name="Acme Corp",
    )
    with pytest.raises(NoEligibleCorridorError) as ei:
        await compare_quotes(payload, {"payments": {"provider": BAD_PROVIDER}})
    assert "provider_not_supported" in str(ei.value)


# ── Admin discoverability ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_test_payments_endpoint_names_the_unsupported_provider():
    """This endpoint is where an admin finds the typo before a payment run
    does, so it must say WHICH name is wrong — not the generic "check your
    configuration" — and must echo no credential from the posted config."""
    from app.api.organization import test_payment_connection

    org = SimpleNamespace(id=uuid.uuid4(), settings={})
    res = await test_payment_connection(
        request={"provider": BAD_PROVIDER, "api_key": "sk_live_SECRET"},
        org=org,
        user=_user(uuid.uuid4()),
    )

    assert res["success"] is False
    assert BAD_PROVIDER in res["message"]
    assert "SECRET" not in res["message"]


@pytest.mark.asyncio
async def test_test_payments_endpoint_still_passes_for_a_supported_provider():
    from app.api.organization import test_payment_connection

    org = SimpleNamespace(id=uuid.uuid4(), settings={})
    res = await test_payment_connection(
        request={"provider": "mock"}, org=org, user=_user(uuid.uuid4())
    )
    assert res["success"] is True


# ── Cash position ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cash_position_balance_degrades_instead_of_raising():
    """`fetch_provider_balance` is best-effort by contract — an unsupported
    provider must fall through to the manual opening balance, not break the
    CFO dashboard."""
    from app.services.cashflow import fetch_provider_balance

    assert await fetch_provider_balance({"provider": BAD_PROVIDER}) is None
    # Unchanged for a real provider.
    balance = await fetch_provider_balance({"provider": "mock"})
    assert balance is not None
    assert balance.provider == "mock"
