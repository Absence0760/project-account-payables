"""CSV importers for pilot-customer data migration.

Lets a new tenant bring their existing vendor list + open AP without
re-entering everything by hand. Runs entirely in-process against the
tenant DB (no S3, no extraction pipeline, no ERP sync) so it's safe to
use on Day 0 before those integrations are wired up.

Public API:
    - ``import_vendors_csv(db, organization_id, csv_text)``
    - ``import_invoices_csv(db, organization_id, csv_text)``

Both return :class:`ImportResult`. Each row is processed independently
— one bad row does not abort the import. Callers are expected to
commit the session themselves after inspecting the result.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice, InvoiceStatus
from app.models.vendor import Vendor

# Supported headers. Column order does not matter; unknown columns are ignored
# so customers can hand us the raw export from their existing tool.
_VENDOR_COLUMNS = {
    "name",
    "code",
    "email",
    "phone",
    "address",
    "tax_id",
    "payment_terms",
    "accepts_virtual_cards",
}

_INVOICE_COLUMNS = {
    "invoice_number",
    "vendor_name",
    "vendor_code",
    "amount",
    "currency",
    "invoice_date",
    "due_date",
    "po_number",
    "description",
    "gl_account",
    "cost_center",
    "status",
}


@dataclass
class ImportRowError:
    row: int
    message: str


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    errors: list[ImportRowError] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "errors": [{"row": e.row, "message": e.message} for e in self.errors],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_rows(csv_text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_text))
    return [
        {
            (k or "").strip().lower(): (v.strip() if isinstance(v, str) else v)
            for k, v in row.items()
        }
        for row in reader
    ]


def _parse_bool(raw: str | None) -> bool:
    if not raw:
        return False
    return raw.strip().lower() in {"1", "true", "t", "yes", "y"}


def _parse_decimal(raw: str | None) -> Decimal | None:
    if raw is None or raw == "":
        return None
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _parse_date(raw: str | None) -> date | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Vendor import
# ---------------------------------------------------------------------------


async def import_vendors_csv(
    db: AsyncSession,
    organization_id: uuid.UUID,
    csv_text: str,
) -> ImportResult:
    """Upsert vendors from CSV. Dedup priority: code > case-insensitive name."""
    result = ImportResult()
    try:
        rows = _read_rows(csv_text)
    except csv.Error as exc:
        result.errors.append(ImportRowError(row=0, message=f"Malformed CSV: {exc}"))
        return result

    for i, row in enumerate(rows, start=2):  # +1 for header, +1 for 1-indexing
        name = (row.get("name") or "").strip()
        if not name:
            result.errors.append(ImportRowError(row=i, message="name is required"))
            continue

        code = (row.get("code") or "").strip() or None
        existing: Vendor | None = None
        if code:
            q = await db.execute(
                select(Vendor).where(
                    Vendor.organization_id == organization_id,
                    Vendor.code == code,
                )
            )
            existing = q.scalar_one_or_none()
        if existing is None:
            q = await db.execute(
                select(Vendor).where(
                    Vendor.organization_id == organization_id,
                    func.lower(Vendor.name) == name.lower(),
                )
            )
            existing = q.scalar_one_or_none()

        if existing is not None:
            result.skipped += 1
            continue

        vendor = Vendor(
            organization_id=organization_id,
            name=name,
            code=code,
            email=(row.get("email") or None) or None,
            phone=(row.get("phone") or None) or None,
            address=(row.get("address") or None) or None,
            tax_id=(row.get("tax_id") or None) or None,
            payment_terms=(row.get("payment_terms") or None) or None,
            accepts_virtual_cards=_parse_bool(row.get("accepts_virtual_cards")),
            status="unverified",
            source="manual",
        )
        db.add(vendor)
        result.imported += 1

    await db.flush()
    return result


# ---------------------------------------------------------------------------
# Invoice import
# ---------------------------------------------------------------------------


async def import_invoices_csv(
    db: AsyncSession,
    organization_id: uuid.UUID,
    csv_text: str,
) -> ImportResult:
    """Import historical invoices. Vendor resolution: code > name. Missing vendors
    get an auto-created stub with status='unverified' so the row still lands."""
    result = ImportResult()
    try:
        rows = _read_rows(csv_text)
    except csv.Error as exc:
        result.errors.append(ImportRowError(row=0, message=f"Malformed CSV: {exc}"))
        return result

    for i, row in enumerate(rows, start=2):
        invoice_number = (row.get("invoice_number") or "").strip()
        vendor_name = (row.get("vendor_name") or "").strip()
        vendor_code = (row.get("vendor_code") or "").strip() or None
        amount = _parse_decimal(row.get("amount"))

        if not invoice_number:
            result.errors.append(ImportRowError(row=i, message="invoice_number is required"))
            continue
        if not vendor_name and not vendor_code:
            result.errors.append(
                ImportRowError(row=i, message="vendor_name or vendor_code is required")
            )
            continue
        if amount is None or amount < 0:
            result.errors.append(
                ImportRowError(row=i, message=f"amount invalid: {row.get('amount')!r}")
            )
            continue

        vendor = await _resolve_or_create_vendor(
            db,
            organization_id=organization_id,
            vendor_name=vendor_name,
            vendor_code=vendor_code,
        )

        status_raw = (row.get("status") or "done").strip().lower()
        try:
            status_val = InvoiceStatus(status_raw)
        except ValueError:
            result.errors.append(ImportRowError(row=i, message=f"status invalid: {status_raw!r}"))
            continue

        # Dedup on (vendor_id, invoice_number) — the natural AP uniqueness key
        q = await db.execute(
            select(Invoice).where(
                Invoice.organization_id == organization_id,
                Invoice.vendor_id == vendor.id,
                Invoice.invoice_number == invoice_number,
            )
        )
        if q.scalar_one_or_none() is not None:
            result.skipped += 1
            continue

        invoice = Invoice(
            organization_id=organization_id,
            invoice_number=invoice_number,
            vendor_name=vendor.name,
            vendor_id=vendor.id,
            amount=amount,
            currency=(row.get("currency") or "USD").upper()[:3],
            invoice_date=_parse_date(row.get("invoice_date")),
            due_date=_parse_date(row.get("due_date")),
            po_number=(row.get("po_number") or None) or None,
            description=(row.get("description") or None) or None,
            gl_account=(row.get("gl_account") or None) or None,
            cost_center=(row.get("cost_center") or None) or None,
            status=status_val,
        )
        db.add(invoice)
        result.imported += 1

    await db.flush()
    return result


async def _resolve_or_create_vendor(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    vendor_name: str,
    vendor_code: str | None,
) -> Vendor:
    if vendor_code:
        q = await db.execute(
            select(Vendor).where(
                Vendor.organization_id == organization_id,
                Vendor.code == vendor_code,
            )
        )
        vendor = q.scalar_one_or_none()
        if vendor is not None:
            return vendor

    if vendor_name:
        q = await db.execute(
            select(Vendor).where(
                Vendor.organization_id == organization_id,
                func.lower(Vendor.name) == vendor_name.lower(),
            )
        )
        vendor = q.scalar_one_or_none()
        if vendor is not None:
            return vendor

    vendor = Vendor(
        organization_id=organization_id,
        name=vendor_name or (vendor_code or "Unknown Vendor"),
        code=vendor_code,
        status="unverified",
        source="manual",
    )
    db.add(vendor)
    await db.flush()
    return vendor
