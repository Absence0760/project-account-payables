"""Real-DB coverage for manually-entered invoices (no OCR/extraction).

`POST /api/invoices` already created a fully-keyed invoice at `new` status
with no file — this pins that path plus the new `POST /{id}/file` companion
endpoint that lets a manually-entered invoice carry its source document.
`attach_invoice_file` is attach-only: it refuses once the invoice already has
a file (409), and it never triggers extraction — a manually-entered invoice
stays exactly as keyed in.
"""

from decimal import Decimal

from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import AuditLog


async def test_manual_create_lands_at_new_with_no_file(realdb):
    org_id = realdb.info("a").org_id
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(
            "/api/invoices",
            json={
                "vendor": "Manual Vendor Co",
                "invoice_number": "MANUAL-001",
                "amount": "500.00",
                "currency": "USD",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "new"
    assert body["file_url"] is None

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == body["id"]))).scalar_one()
        assert inv.organization_id == org_id
        assert inv.status == InvoiceStatus.new
        assert inv.amount == Decimal("500.00")
        assert inv.file_key is None


async def test_ap_clerk_cannot_manually_create_an_invoice(realdb):
    """`create_invoice` is gated to admin/ap_manager/cfo — an ap_clerk (the
    role that normally just uploads/extracts) is refused."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/invoices",
            json={"vendor": "X", "invoice_number": "CLERK-001", "amount": "1.00"},
        )
    assert resp.status_code == 403


async def test_attach_file_to_manual_invoice_succeeds_once(realdb):
    async with realdb.client(key="a", role="admin") as c:
        create = await c.post(
            "/api/invoices",
            json={
                "vendor": "File Vendor Co",
                "invoice_number": "MANUAL-FILE-001",
                "amount": "125.00",
            },
        )
        invoice_id = create.json()["id"]

        attach = await c.post(
            f"/api/invoices/{invoice_id}/file",
            files={"file": ("invoice.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )
        assert attach.status_code == 201, attach.text
        body = attach.json()
        assert body["file_url"] is not None
        assert body["file_url"].startswith("/api/invoices/file/")
        # No extraction ran — manual entry keeps its keyed-in fields verbatim.
        assert body["status"] == "new"
        assert body["invoice_number"] == "MANUAL-FILE-001"

        # A second attach attempt is refused — attach-only, not replace.
        again = await c.post(
            f"/api/invoices/{invoice_id}/file",
            files={"file": ("other.pdf", b"%PDF-1.4 other", "application/pdf")},
        )
        assert again.status_code == 409

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog).where(
                        AuditLog.entity_id == invoice_id,
                        AuditLog.action == "invoice.file_attached",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].details["filename"] == "invoice.pdf"


async def test_attach_file_rejects_disallowed_content_type(realdb):
    async with realdb.client(key="a", role="admin") as c:
        create = await c.post(
            "/api/invoices",
            json={"vendor": "V", "invoice_number": "MANUAL-BAD-001", "amount": "1.00"},
        )
        invoice_id = create.json()["id"]

        resp = await c.post(
            f"/api/invoices/{invoice_id}/file",
            files={"file": ("virus.exe", b"MZ\x90\x00", "application/x-msdownload")},
        )
    assert resp.status_code == 400

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == invoice_id))).scalar_one()
        assert inv.file_key is None
