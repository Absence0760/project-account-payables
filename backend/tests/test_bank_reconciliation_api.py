"""Real-DB coverage for the bank-reconciliation router
(``app/api/bank_reconciliation.py``).

Before this router existed, ``app/services/bank_reconciliation.py`` — the CSV
importer + payment matcher — had a migration, a model, a docs page, and its
own service-level test suite (``test_bank_reconciliation.py``), but **no HTTP
route ever mounted it** (see ``git log -- app/api/bank_reconciliation.py``:
no such file existed before this change). The whole feature was unreachable
by any user or the frontend. This suite proves the wiring: upload → persisted
statement + matched transactions, list/detail, manual match review, delete,
and RBAC — end-to-end against the live test tenants. The parse/match math
itself is owned by the (separately tested) pure service.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.models.bank_reconciliation import BankStatement, BankTransaction
from app.models.entity import Entity
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor
from app.models.workflow import AuditLog

_TODAY = date.today()


async def _default_entity_id(s):
    return (
        await s.execute(select(Entity.id).where(Entity.is_default.is_(True)).limit(1))
    ).scalar_one()


async def _add_payment(
    mk,
    org_id,
    *,
    amount="500.00",
    provider_payment_id=None,
    reference=None,
    submitted_at=None,
    vendor_name="Acme Supplies",
) -> str:
    async with mk() as s:
        entity_id = await _default_entity_id(s)
        vendor = Vendor(organization_id=org_id, name=vendor_name, entity_id=entity_id)
        s.add(vendor)
        await s.flush()
        inv = Invoice(
            organization_id=org_id,
            entity_id=entity_id,
            invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
            vendor_name=vendor_name,
            vendor_id=vendor.id,
            amount=Decimal(amount),
            currency="USD",
            invoice_date=_TODAY,
            due_date=_TODAY + timedelta(days=30),
            status=InvoiceStatus.paid,
        )
        s.add(inv)
        await s.flush()
        pay = Payment(
            invoice_id=inv.id,
            entity_id=entity_id,
            amount=Decimal(amount),
            method="ach",
            status="completed",
            provider_payment_id=provider_payment_id,
            reference=reference,
            submitted_at=submitted_at or datetime.now(UTC),
        )
        s.add(pay)
        await s.commit()
        await s.refresh(pay)
        return str(pay.id)


def _csv(*lines: str) -> bytes:
    return ("\r\n".join(lines) + "\r\n").encode()


# ---------------------------------------------------------------------------
# Upload — happy path + matching
# ---------------------------------------------------------------------------


async def test_upload_matches_by_reference_and_persists_audit(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    payment_id = await _add_payment(mk, org_id, amount="500.00", provider_payment_id="TRACE-123")

    csv_body = _csv(
        "Date,Amount,Reference,Description",
        f"{_TODAY.isoformat()},-500.00,TRACE-123,Vendor ACH",
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****1234",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
                "currency": "USD",
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_format"] == "csv"
    assert body["file_key"] is None  # raw-file storage deferred, like vendor-statement-recon
    assert body["transaction_count"] == 1
    assert body["matched_count"] == 1
    tx = body["transactions"][0]
    assert tx["matched_payment_id"] == payment_id
    assert tx["match_method"] == "provider_id"
    assert tx["match_confidence"] == 100.0
    assert tx["direction"] == "debit"
    assert tx["amount"] == 500.0

    # Persisted, not just shaped in the response.
    async with mk() as s:
        stmt = (
            await s.execute(select(BankStatement).where(BankStatement.id == uuid.UUID(body["id"])))
        ).scalar_one()
        assert stmt.matched_count == 1
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "bank_reconciliation.imported",
                    AuditLog.entity_id == stmt.id,
                )
            )
        ).scalar_one()
        assert audit.entity_type == "bank_statement"


async def test_upload_credits_are_skipped_not_matched(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_payment(mk, org_id, amount="500.00", provider_payment_id="TRACE-999")

    csv_body = _csv(
        "Date,Amount,Reference,Description",
        f"{_TODAY.isoformat()},250.00,TRACE-OTHER,Incoming refund",  # positive = credit
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****1234",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["matched_count"] == 0
    assert body["transactions"][0]["direction"] == "credit"
    assert body["transactions"][0]["matched_payment_id"] is None


async def test_upload_malformed_csv_returns_422(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****1234",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
            },
            files={"file": ("bad.csv", b"not,a,valid,header\r\n", "text/csv")},
        )
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------


async def test_list_and_get_detail(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    await _add_payment(mk, org_id, amount="100.00")

    csv_body = _csv("Date,Amount,Description", f"{_TODAY.isoformat()},-100.00,Some payment")
    async with realdb.client(key="a", role="ap_manager") as c:
        create_resp = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****5555",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
        stmt_id = create_resp.json()["id"]

        list_resp = await c.get(
            "/api/bank-reconciliation", params={"account_identifier": "Operating ****5555"}
        )
        assert list_resp.status_code == 200
        list_body = list_resp.json()
        assert any(item["id"] == stmt_id for item in list_body["items"])
        # List omits the transaction detail (avoids an N+1 payload on the index).
        assert list_body["items"][0]["transactions"] is None

        detail_resp = await c.get(f"/api/bank-reconciliation/{stmt_id}")
        assert detail_resp.status_code == 200
        detail_body = detail_resp.json()
        assert len(detail_body["transactions"]) == 1


async def test_get_unknown_statement_is_404(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/bank-reconciliation/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Manual match review
# ---------------------------------------------------------------------------


async def test_resolve_transaction_sets_and_clears_manual_match(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    payment_id = await _add_payment(mk, org_id, amount="777.00")

    # Nothing in the CSV lines up automatically (no reference, ambiguous
    # amount+date isn't even attempted since there's no candidate payment at
    # this amount+window unless we look it up) — upload unmatched, then let
    # the AP clerk resolve it by hand.
    csv_body = _csv("Date,Amount,Description", f"{_TODAY.isoformat()},-321.00,Unmatched wire")
    async with realdb.client(key="a", role="ap_manager") as c:
        up = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****9999",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
        stmt_id = up.json()["id"]
        tx_id = up.json()["transactions"][0]["id"]
        assert up.json()["matched_count"] == 0

        resolve = await c.post(
            f"/api/bank-reconciliation/{stmt_id}/transactions/{tx_id}/resolve",
            json={"matched_payment_id": payment_id},
        )
        assert resolve.status_code == 200, resolve.text
        body = resolve.json()
        assert body["matched_count"] == 1
        tx = body["transactions"][0]
        assert tx["matched_payment_id"] == payment_id
        assert tx["match_method"] == "manual"
        assert tx["match_confidence"] == 100.0

        # Clear it back to unmatched.
        cleared = await c.post(
            f"/api/bank-reconciliation/{stmt_id}/transactions/{tx_id}/resolve",
            json={"matched_payment_id": None},
        )
        assert cleared.status_code == 200
        cleared_body = cleared.json()
        assert cleared_body["matched_count"] == 0
        assert cleared_body["transactions"][0]["matched_payment_id"] is None
        assert cleared_body["transactions"][0]["match_method"] is None

    async with mk() as s:
        audits = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.action == "bank_reconciliation.transaction_resolved",
                        AuditLog.entity_id == uuid.UUID(stmt_id),
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(audits) == 2  # set + clear


async def test_resolve_unknown_payment_is_404(realdb):
    csv_body = _csv("Date,Amount,Description", f"{_TODAY.isoformat()},-50.00,X")
    async with realdb.client(key="a", role="ap_manager") as c:
        up = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****0001",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
        stmt_id = up.json()["id"]
        tx_id = up.json()["transactions"][0]["id"]

        resp = await c.post(
            f"/api/bank-reconciliation/{stmt_id}/transactions/{tx_id}/resolve",
            json={"matched_payment_id": str(uuid.uuid4())},
        )
    assert resp.status_code == 404


async def test_resolve_refuses_to_double_claim_a_payment(realdb):
    """A Payment can be matched to at most one BankTransaction — the same
    invariant the automatic matcher enforces. Manually resolving a second
    transaction onto an already-matched payment must be refused (409), not
    silently steal/duplicate the match."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    payment_id = await _add_payment(mk, org_id, amount="42.00")

    csv_body = _csv(
        "Date,Amount,Description",
        f"{_TODAY.isoformat()},-11.00,First",
        f"{_TODAY.isoformat()},-12.00,Second",
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        up = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****7777",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
        stmt_id = up.json()["id"]
        tx1_id, tx2_id = (t["id"] for t in up.json()["transactions"])

        first = await c.post(
            f"/api/bank-reconciliation/{stmt_id}/transactions/{tx1_id}/resolve",
            json={"matched_payment_id": payment_id},
        )
        assert first.status_code == 200, first.text

        second = await c.post(
            f"/api/bank-reconciliation/{stmt_id}/transactions/{tx2_id}/resolve",
            json={"matched_payment_id": payment_id},
        )
        assert second.status_code == 409, second.text

        # The first transaction's match is untouched by the refused attempt.
        detail = await c.get(f"/api/bank-reconciliation/{stmt_id}")
        by_id = {t["id"]: t for t in detail.json()["transactions"]}
        assert by_id[tx1_id]["matched_payment_id"] == payment_id
        assert by_id[tx2_id]["matched_payment_id"] is None


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


async def test_delete_cascades_transactions_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    csv_body = _csv(
        "Date,Amount,Description",
        f"{_TODAY.isoformat()},-10.00,A",
        f"{_TODAY.isoformat()},-20.00,B",
    )
    async with realdb.client(key="a", role="ap_manager") as c:
        up = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****4242",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
        stmt_id = up.json()["id"]

        del_resp = await c.delete(f"/api/bank-reconciliation/{stmt_id}")
        assert del_resp.status_code == 204

        get_resp = await c.get(f"/api/bank-reconciliation/{stmt_id}")
        assert get_resp.status_code == 404

    async with mk() as s:
        remaining = (
            (
                await s.execute(
                    select(BankTransaction).where(
                        BankTransaction.statement_id == uuid.UUID(stmt_id)
                    )
                )
            )
            .scalars()
            .all()
        )
        assert remaining == []
        audit = (
            await s.execute(
                select(AuditLog).where(
                    AuditLog.action == "bank_reconciliation.deleted",
                    AuditLog.entity_id == uuid.UUID(stmt_id),
                )
            )
        ).scalar_one()
        assert audit.entity_type == "bank_statement"


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


async def test_ap_clerk_can_read_but_not_upload_or_delete(realdb):
    csv_body = _csv("Date,Amount,Description", f"{_TODAY.isoformat()},-10.00,A")
    async with realdb.client(key="a", role="ap_clerk") as c:
        list_resp = await c.get("/api/bank-reconciliation")
        assert list_resp.status_code == 200

        upload_resp = await c.post(
            "/api/bank-reconciliation/upload",
            data={
                "account_identifier": "Operating ****4242",
                "period_start": (_TODAY - timedelta(days=30)).isoformat(),
                "period_end": _TODAY.isoformat(),
            },
            files={"file": ("statement.csv", csv_body, "text/csv")},
        )
        assert upload_resp.status_code == 403

        delete_resp = await c.delete(f"/api/bank-reconciliation/{uuid.uuid4()}")
        assert delete_resp.status_code == 403
