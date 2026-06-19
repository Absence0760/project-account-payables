"""Real-DB coverage for the Positive Pay router (``app/api/positive_pay.py``).

Exercises check-issue generation (+ idempotency on the (run, format) slot), the
ACH-authorization file, list / detail / download (with the cross-tenant download
gate), return processing (raising a deduped ``fraud_flag`` Exception on an
altered cheque + a standalone invoice-less one for a never-issued cheque), and
the RBAC read/write split — end-to-end against the live test tenants. The formatter +
classifier math is owned by the separately-tested ``positive_pay`` service; here
we prove the HTTP surface wires it through with exact ``Numeric`` money and the
PII invariant (no account number in the audit trail).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.models.entity import Entity
from app.models.exception import Exception as APException
from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization
from app.models.payment import Payment, PaymentRun
from app.models.vendor import Vendor
from app.models.workflow import AuditLog
from app.services import storage

_TODAY = date.today()

# A full account number — must NEVER appear in the audit trail.
_ACCOUNT_NUMBER = "9876543210"


async def _default_entity_id(s):
    return (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)).limit(1))
    ).scalar_one()


async def _set_check_account(realdb, org_id, account=_ACCOUNT_NUMBER, company="Acme Corp"):
    """Stamp the originating cheque account + company name on the control-plane
    Organization.settings (the router reads them from there)."""
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings = dict(org.settings or {})
        settings["company"] = {**(settings.get("company") or {}), "name": company}
        settings["payments"] = {
            **(settings.get("payments") or {}),
            "check_account_number": account,
        }
        org.settings = settings
        await s.commit()


async def _clear_settings(realdb, org_id):
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        settings = dict(org.settings or {})
        settings.pop("payments", None)
        settings.pop("company", None)
        org.settings = settings
        await s.commit()


async def _add_vendor(mk, org_id, *, name="Globex Industrial", bank_details=None, status="active"):
    async with mk() as s:
        v = Vendor(
            organization_id=org_id,
            name=name,
            status=status,
            bank_details=bank_details,
            entity_id=await _default_entity_id(s),
        )
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return str(v.id)


async def _add_invoice(mk, org_id, *, vendor_id, invoice_number, amount="1000.00"):
    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            entity_id=await _default_entity_id(s),
            invoice_number=invoice_number,
            vendor_name="Globex Industrial",
            vendor_id=uuid.UUID(vendor_id),
            amount=Decimal(amount),
            currency="USD",
            invoice_date=_TODAY,
            due_date=_TODAY + timedelta(days=30),
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return str(inv.id)


async def _add_check_run(mk, org_id, *, invoice_id, check_number="CHK1001", amount="1000.00"):
    """Create an executed PaymentRun with one completed check-method Payment."""
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        run = PaymentRun(
            organization_id=org_id,
            entity_id=entity_id,
            status="executed",
            total_amount=Decimal(amount),
            executed_at=datetime.now(UTC),
        )
        s.add(run)
        await s.flush()
        pay = Payment(
            entity_id=entity_id,
            invoice_id=uuid.UUID(invoice_id),
            payment_run_id=run.id,
            amount=Decimal(amount),
            method="check",
            status="completed",
            reference=check_number,
        )
        s.add(pay)
        await s.commit()
        await s.refresh(run)
        return str(run.id)


# ---------------------------------------------------------------------------
# check-issue generation + idempotency
# ---------------------------------------------------------------------------


async def test_generate_check_issue_creates_file_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        vendor_id = await _add_vendor(mk, org_id)
        invoice_id = await _add_invoice(
            mk, org_id, vendor_id=vendor_id, invoice_number="INV-1", amount="1234.56"
        )
        run_id = await _add_check_run(
            mk, org_id, invoice_id=invoice_id, check_number="CHK1001", amount="1234.56"
        )

        async with realdb.client(key="a", role="ap_manager") as c:
            resp = await c.post(
                f"/api/positive-pay/payment-runs/{run_id}/check-issue", json={"bank_format": "csv"}
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["file_type"] == "check_issue"
        assert body["item_count"] == 1
        assert body["total_amount"] == 1234.56
        assert body["account_last4"] == "3210"
        assert body["payment_run_id"] == run_id
        assert body["file_key"] is not None

        async with mk() as s:
            audit = (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "positive_pay.check_issue_generated",
                        AuditLog.entity_id == uuid.UUID(body["id"]),
                    )
                )
            ).scalar_one()
            assert audit.entity_type == "positive_pay_file"
            # PII invariant — no full account number in the audit details.
            assert _ACCOUNT_NUMBER not in str(audit.details)
    finally:
        await _clear_settings(realdb, org_id)


async def test_generate_check_issue_is_idempotent(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        vendor_id = await _add_vendor(mk, org_id)
        invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="INV-2")
        run_id = await _add_check_run(mk, org_id, invoice_id=invoice_id, check_number="CHK2001")

        async with realdb.client(key="a", role="ap_manager") as c:
            first = await c.post(f"/api/positive-pay/payment-runs/{run_id}/check-issue", json={})
            second = await c.post(f"/api/positive-pay/payment-runs/{run_id}/check-issue", json={})
        assert first.status_code == 201
        assert second.status_code == 200
        assert first.json()["id"] == second.json()["id"]

        async with mk() as s:
            from app.models.positive_pay import PositivePayFile

            rows = (
                (
                    await s.execute(
                        select(PositivePayFile).where(
                            PositivePayFile.payment_run_id == uuid.UUID(run_id)
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(rows) == 1
    finally:
        await _clear_settings(realdb, org_id)


async def test_generate_check_issue_404_unknown_run(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/positive-pay/payment-runs/{uuid.uuid4()}/check-issue", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# list / detail / download + cross-tenant gate
# ---------------------------------------------------------------------------


async def test_list_detail_and_download(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        vendor_id = await _add_vendor(mk, org_id)
        invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="INV-3")
        run_id = await _add_check_run(mk, org_id, invoice_id=invoice_id, check_number="CHK3001")

        async with realdb.client(key="a", role="ap_manager") as c:
            file_id = (
                await c.post(f"/api/positive-pay/payment-runs/{run_id}/check-issue", json={})
            ).json()["id"]

        # cfo can read.
        async with realdb.client(key="a", role="cfo") as c:
            listing = (await c.get("/api/positive-pay?file_type=check_issue")).json()
            assert any(i["id"] == file_id for i in listing["items"])
            assert listing["total"] >= 1

            detail = await c.get(f"/api/positive-pay/{file_id}")
            assert detail.status_code == 200

            dl = await c.get(f"/api/positive-pay/{file_id}/download")
            assert dl.status_code == 200
            assert dl.headers["content-type"].startswith("text/csv")
            text = dl.content.decode("utf-8")
            assert "check_number,payee,amount,issue_date,account_number" in text
            assert "CHK3001" in text
    finally:
        await _clear_settings(realdb, org_id)


async def test_download_cross_tenant_404(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        vendor_id = await _add_vendor(mk, org_id)
        invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="INV-4")
        run_id = await _add_check_run(mk, org_id, invoice_id=invoice_id, check_number="CHK4001")
        async with realdb.client(key="a", role="ap_manager") as c:
            file_id = (
                await c.post(f"/api/positive-pay/payment-runs/{run_id}/check-issue", json={})
            ).json()["id"]
        # Tenant b cannot fetch tenant a's file — scoped 404 (no enumeration).
        async with realdb.client(key="b", role="ap_manager") as c:
            resp = await c.get(f"/api/positive-pay/{file_id}/download")
        assert resp.status_code == 404
    finally:
        await _clear_settings(realdb, org_id)


# ---------------------------------------------------------------------------
# ACH authorization
# ---------------------------------------------------------------------------


async def test_generate_ach_authorization(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    # One ACH-capable vendor + one without bank details (skipped).
    await _add_vendor(
        mk,
        org_id,
        name="ACH Vendor",
        bank_details={"routing_number": "021000021", "account_number": "111222333"},
    )
    await _add_vendor(mk, org_id, name="No-Bank Vendor")

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/positive-pay/ach-authorization", json={"bank_format": "csv"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["file_type"] == "ach_authorization"
    assert body["payment_run_id"] is None
    assert body["item_count"] == 1

    async with realdb.client(key="a", role="ap_manager") as c:
        dl = await c.get(f"/api/positive-pay/{body['id']}/download")
    text = dl.content.decode("utf-8")
    assert "vendor_name,routing_number,account_number,status" in text
    assert "ACH Vendor" in text
    assert "No-Bank Vendor" not in text


# ---------------------------------------------------------------------------
# process return → fraud_flag Exception + dedupe + unmatched
# ---------------------------------------------------------------------------


async def test_process_return_raises_fraud_including_standalone_not_on_file(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _set_check_account(realdb, org_id)
    try:
        vendor_id = await _add_vendor(mk, org_id)
        invoice_id = await _add_invoice(
            mk, org_id, vendor_id=vendor_id, invoice_number="INV-5", amount="500.00"
        )
        run_id = await _add_check_run(
            mk, org_id, invoice_id=invoice_id, check_number="CHK5001", amount="500.00"
        )

        async with realdb.client(key="a", role="ap_manager") as c:
            file_id = (
                await c.post(f"/api/positive-pay/payment-runs/{run_id}/check-issue", json={})
            ).json()["id"]

            # Present an ALTERED amount (500 → 900) + a cheque we never issued.
            resp = await c.post(
                f"/api/positive-pay/{file_id}/process-return",
                json={
                    "presented_items": [
                        {"check_number": "CHK5001", "amount": "900.00"},  # altered
                        {"check_number": "CHK9999", "amount": "42.00"},  # not on file
                    ]
                },
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["presented_count"] == 2
        assert body["amount_mismatches"] == 1
        assert body["not_on_file"] == 1
        # BOTH fraud signals raise an exception now — the never-issued cheque is
        # a standalone (invoice-less) fraud_flag, not a buried meta entry.
        assert body["exceptions_created"] == 2
        assert "unmatched" not in body
        assert body["file"]["status"] == "returned_processed"
        assert "unmatched_returns" not in (body["file"]["meta"] or {})

        async with mk() as s:
            # Invoice-scoped fraud_flag for the altered cheque.
            linked = (
                (
                    await s.execute(
                        select(APException).where(
                            APException.invoice_id == uuid.UUID(invoice_id),
                            APException.exception_type == "fraud_flag",
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(linked) == 1
            assert linked[0].severity == "error"
            assert linked[0].status == "open"

            # Standalone (invoice_id IS NULL) fraud_flag for the never-issued cheque.
            standalone = (
                (
                    await s.execute(
                        select(APException).where(
                            APException.invoice_id.is_(None),
                            APException.exception_type == "fraud_flag",
                            APException.organization_id == org_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert len(standalone) == 1
            assert "CHK9999" in (standalone[0].description or "")
            assert standalone[0].status == "open"
            # PII invariant — no account number in either fraud description.
            assert _ACCOUNT_NUMBER not in (linked[0].description or "")
            assert _ACCOUNT_NUMBER not in (standalone[0].description or "")
            # return-processed audit row carries no account number either.
            audit = (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "positive_pay.return_processed",
                        AuditLog.entity_id == uuid.UUID(file_id),
                    )
                )
            ).scalar_one()
            assert _ACCOUNT_NUMBER not in str(audit.details)

        # The standalone fraud exception surfaces in the queue with a null invoice_id.
        async with realdb.client(key="a", role="ap_manager") as c:
            listing = (await c.get("/api/exceptions?type=fraud_flag")).json()
        standalone_rows = [
            r
            for r in listing["items"]
            if r["invoice_id"] is None and "CHK9999" in (r["description"] or "")
        ]
        assert len(standalone_rows) == 1
        assert standalone_rows[0]["invoice_number"] is None

        # Re-running the return must NOT duplicate either fraud_flag exception.
        async with realdb.client(key="a", role="ap_manager") as c:
            again = await c.post(
                f"/api/positive-pay/{file_id}/process-return",
                json={
                    "presented_items": [
                        {"check_number": "CHK5001", "amount": "900.00"},
                        {"check_number": "CHK9999", "amount": "42.00"},
                    ]
                },
            )
        assert again.status_code == 200
        assert again.json()["exceptions_created"] == 0

        async with mk() as s:
            total_fraud = (
                await s.execute(
                    select(func.count()).where(
                        APException.exception_type == "fraud_flag",
                        APException.organization_id == org_id,
                    )
                )
            ).scalar()
            assert total_fraud == 2
    finally:
        await _clear_settings(realdb, org_id)


async def test_agent_resolve_422_on_invoiceless_exception(realdb):
    """A standalone (invoice-less) fraud exception can't be agent-resolved."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with mk() as s:
        exc = APException(
            invoice_id=None,
            exception_type="fraud_flag",
            severity="error",
            status="open",
            description="Positive Pay return: check CHK-ORPHAN not on issued file",
            organization_id=org_id,
        )
        s.add(exc)
        await s.commit()
        exc_id = str(exc.id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/exceptions/{exc_id}/agent-resolve")
    assert resp.status_code == 422
    assert "no associated invoice" in resp.json()["detail"]


async def test_process_return_422_on_ach_file(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_vendor(
        mk,
        org_id,
        name="ACH Only",
        bank_details={"routing_number": "021000021", "account_number": "555"},
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        file_id = (await c.post("/api/positive-pay/ach-authorization", json={})).json()["id"]
        resp = await c.post(
            f"/api/positive-pay/{file_id}/process-return",
            json={"presented_items": []},
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


async def test_delete_file(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_vendor(
        mk,
        org_id,
        name="ACH Del",
        bank_details={"routing_number": "021000021", "account_number": "777"},
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        created = (await c.post("/api/positive-pay/ach-authorization", json={})).json()
        file_id = created["id"]
        file_key = created["file_key"]
        # The object holds full account / routing numbers — confirm it exists,
        # then that delete purges it from MinIO (not just the DB row), so no
        # PII-bearing bytes linger at rest.
        assert storage.get_file(file_key)[0]
        resp = await c.delete(f"/api/positive-pay/{file_id}")
        assert resp.status_code == 204
        gone = await c.get(f"/api/positive-pay/{file_id}")
        assert gone.status_code == 404
    with pytest.raises(Exception):
        storage.get_file(file_key)


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_clerk_cannot_generate(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id = await _add_vendor(mk, org_id)
    invoice_id = await _add_invoice(mk, org_id, vendor_id=vendor_id, invoice_number="INV-RB")
    run_id = await _add_check_run(mk, org_id, invoice_id=invoice_id, check_number="CHKRB01")
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/positive-pay/payment-runs/{run_id}/check-issue", json={})
    assert resp.status_code == 403


async def test_cfo_can_read_list(realdb):
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.get("/api/positive-pay")
    assert resp.status_code == 200
