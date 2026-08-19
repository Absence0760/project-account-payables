"""Three round-11 money-path follow-ups on `api/payments.py`.

1. **`void_payment` overwrote the regulated `completed_at`.** Voiding a
   `completed` payment stamped the void instant onto the settlement
   timestamp, destroying the only record of when the money actually moved —
   and the audit row captured `previous_status` but not the previous
   timestamp, so it was unrecoverable. `/retry-failed` explicitly refuses to
   overwrite the same field and says why.

2. **`/compliance/release` didn't guard `_execute_single_payment`.** Every
   other caller wraps it because "a live FX / sanctions / processor adapter
   can raise anything", and marks the payment `failed` so the attempt is
   recorded. Here it propagated: FastAPI 500ed, the session rolled back, and
   the payment reverted to `pending_compliance` with no record even if the
   processor had already accepted the order.

3. **`_execute_single_payment` skipped the entire compliance gate when the
   invoice was missing.** The credit-memo re-check, the FX leg and the
   sanctions/KYC gate are each written `if invoice is not None`, so an
   invoice-less payment fell straight through to `adapter.create_payment`
   with an empty `invoice_number` / `vendor_name` — money to a payee nobody
   screened. That is the inverse of the two "no screenable vendor → hold,
   never pay unscreened" branches directly below it.

Requires the dev Postgres (`pnpm db:up`); skips otherwise.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor

pytestmark = pytest.mark.asyncio

TENANT = "a"


def _user(uid: uuid.UUID):
    return SimpleNamespace(id=uid, full_name="Timestamp Tester", roles=["admin"])


def _org(org_id: uuid.UUID):
    return SimpleNamespace(
        id=org_id,
        name="PyTest",
        slug="pytesta",
        settings={"payments": {"provider": "mock"}},
    )


async def _seed_invoice(session_mk, org_id: uuid.UUID, *, amount: Decimal) -> uuid.UUID:
    inv_id = uuid.uuid4()
    vendor_id = uuid.uuid4()
    async with session_mk() as s:
        s.add(Vendor(id=vendor_id, name="Acme Corp", organization_id=org_id))
        s.add(
            Invoice(
                id=inv_id,
                invoice_number=f"TS-{uuid.uuid4().hex[:8]}",
                vendor_name="Acme Corp",
                vendor_id=vendor_id,
                amount=amount,
                currency="USD",
                status=InvoiceStatus.payment_scheduled,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
            )
        )
        await s.commit()
    return inv_id


async def _seed_payment(session_mk, invoice_id, *, status: str, completed_at) -> uuid.UUID:
    payment_id = uuid.uuid4()
    async with session_mk() as s:
        s.add(
            Payment(
                id=payment_id,
                invoice_id=invoice_id,
                payment_run_id=None,
                amount=Decimal("250.00"),
                method="ach",
                status=status,
                provider="mock",
                provider_payment_id="px_live_1",
                submitted_at=completed_at,
                completed_at=completed_at,
                correlation_id=uuid.uuid4(),
            )
        )
        await s.commit()
    return payment_id


# --------------------------------------------------------------------------- #
# 1. void must not destroy the settlement timestamp
# --------------------------------------------------------------------------- #


async def test_void_preserves_the_regulated_completed_at(realdb):
    from app.api.payments import VoidPaymentRequest, void_payment

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_invoice(mk, info.org_id, amount=Decimal("250.00"))
    settled_at = datetime.now(UTC) - timedelta(days=3)
    payment_id = await _seed_payment(mk, invoice_id, status="completed", completed_at=settled_at)

    adapter = SimpleNamespace(provider_name="mock", void_payment=AsyncMock(return_value=True))
    async with mk() as db:
        with patch("app.api.payments.get_payment_adapter", return_value=adapter):
            await void_payment(
                payment_id=payment_id,
                body=VoidPaymentRequest(reason="duplicate"),
                db=db,
                org=_org(info.org_id),
                user=_user(info.users["admin"]),
                entity_id=None,
            )

    async with mk() as s:
        payment = (await s.execute(select(Payment).where(Payment.id == payment_id))).scalar_one()
        assert payment.status == "voided"
        # The settlement timestamp survives the void — it says when the money
        # moved, and the money did move.
        assert payment.completed_at is not None
        assert abs((payment.completed_at - settled_at).total_seconds()) < 1


async def test_void_of_a_never_settled_payment_still_gets_a_terminal_timestamp(realdb):
    """A payment that never settled has no settlement time to protect, so the
    void is still recorded as its terminal instant."""
    from app.api.payments import VoidPaymentRequest, void_payment

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_invoice(mk, info.org_id, amount=Decimal("250.00"))
    payment_id = await _seed_payment(mk, invoice_id, status="submitted", completed_at=None)

    adapter = SimpleNamespace(provider_name="mock", void_payment=AsyncMock(return_value=True))
    async with mk() as db:
        with patch("app.api.payments.get_payment_adapter", return_value=adapter):
            await void_payment(
                payment_id=payment_id,
                body=VoidPaymentRequest(reason="rail never confirmed"),
                db=db,
                org=_org(info.org_id),
                user=_user(info.users["admin"]),
                entity_id=None,
            )

    async with mk() as s:
        payment = (await s.execute(select(Payment).where(Payment.id == payment_id))).scalar_one()
        assert payment.status == "voided"
        assert payment.completed_at is not None


async def test_void_audit_row_records_the_void_instant(realdb):
    """The void instant moves to the audit row rather than onto `completed_at`."""
    from app.api.payments import VoidPaymentRequest, void_payment
    from app.models.workflow import AuditLog

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_invoice(mk, info.org_id, amount=Decimal("250.00"))
    settled_at = datetime.now(UTC) - timedelta(days=3)
    payment_id = await _seed_payment(mk, invoice_id, status="completed", completed_at=settled_at)

    adapter = SimpleNamespace(provider_name="mock", void_payment=AsyncMock(return_value=True))
    async with mk() as db:
        with patch("app.api.payments.get_payment_adapter", return_value=adapter):
            await void_payment(
                payment_id=payment_id,
                body=VoidPaymentRequest(reason="duplicate"),
                db=db,
                org=_org(info.org_id),
                user=_user(info.users["admin"]),
                entity_id=None,
            )

    async with mk() as s:
        row = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.entity_id == payment_id,
                    AuditLog.action == "payment.voided",
                )
            )
        ).scalar_one()
        assert row.details["voided_at"]
        assert row.details["settled_at"]
        assert row.details["previous_status"] == "completed"


# --------------------------------------------------------------------------- #
# 2. /compliance/release must record a raising adapter, not 500
# --------------------------------------------------------------------------- #


async def test_compliance_release_records_an_adapter_raise_as_failed(realdb):
    from app.api.payments import release_compliance_hold

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    invoice_id = await _seed_invoice(mk, info.org_id, amount=Decimal("250.00"))
    payment_id = await _seed_payment(mk, invoice_id, status="pending_compliance", completed_at=None)

    adapter = SimpleNamespace(provider_name="mock")

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("processor exploded with acct ****1234")

    async with mk() as db:
        with (
            patch("app.api.payments._require_payment_adapter", return_value=adapter),
            patch("app.api.payments._execute_single_payment", side_effect=_boom),
        ):
            result = await release_compliance_hold(
                payment_id=payment_id,
                db=db,
                org=_org(info.org_id),
                user=_user(info.users["admin"]),
                entity_id=None,
            )

    # No 500 — the attempt is recorded.
    assert result.status == "failed"
    async with mk() as s:
        payment = (await s.execute(select(Payment).where(Payment.id == payment_id))).scalar_one()
        assert payment.status == "failed"
        # PII-out-of-logs / out-of-rows: only the exception CLASS, never the
        # adapter's message (which can carry a partial account number or PAN).
        assert payment.failure_reason == "unexpected_error:RuntimeError"
        assert "1234" not in (payment.failure_reason or "")


# --------------------------------------------------------------------------- #
# 3. an invoice-less payment must never reach the processor
# --------------------------------------------------------------------------- #


async def test_execute_single_payment_fails_closed_without_an_invoice(realdb):
    """No invoice → no payee to screen, no amount to re-verify, no rate to
    lock. The adapter must not be called at all."""
    from app.api.payments import _execute_single_payment

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)

    payment = SimpleNamespace(
        id=uuid.uuid4(),
        invoice_id=uuid.uuid4(),  # points at nothing
        amount=Decimal("500.00"),
        method="ach",
        status="pending",
        failure_reason=None,
        completed_at=None,
        submitted_at=None,
        provider=None,
        provider_payment_id=None,
        reference=None,
        correlation_id=uuid.uuid4(),
        source_amount=None,
        source_currency=None,
        fx_rate=None,
        fx_locked_at=None,
        corridor=None,
        target_country=None,
        payment_run_id=None,
    )
    adapter = SimpleNamespace(provider_name="mock", create_payment=AsyncMock())

    async with mk() as db:
        await _execute_single_payment(
            db,
            payment=payment,
            org=_org(info.org_id),
            adapter=adapter,
            user=_user(info.users["admin"]),
            now=datetime.now(UTC),
        )

    adapter.create_payment.assert_not_awaited()
    assert payment.status == "failed"
    assert (payment.failure_reason or "").startswith("invoice_missing")
