"""CSV importers for pilot-customer data migration.

Lets a new tenant bring their existing vendor list + open AP without
re-entering everything by hand. Runs entirely in-process against the
tenant DB (no S3, no extraction pipeline, no ERP sync) so it's safe to
use on Day 0 before those integrations are wired up.

Public API:
    - ``import_vendors_csv(db, organization_id, csv_text)``
    - ``import_invoices_csv(db, organization_id, csv_text)``
    - ``import_corporate_card_csv(db, organization_id, csv_text)``

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
from app.services.numeric_bounds import MONEY_NUMERIC, fits_numeric
from app.utils.dates import parse_ambiguous_date

# CSV imports are always small structured text (vendor lists, invoice batches,
# card-transaction feeds) — well under the general 25 MB file-upload cap in
# ``app/services/storage.py``. A tighter 10 MB limit still comfortably covers
# any realistic export while bounding memory use before ``file.read()``
# buffers the whole upload (the read happens in the API layer, ahead of the
# parsing this module does; see backend/docs/csv-import.md).
MAX_CSV_IMPORT_SIZE = 10 * 1024 * 1024  # 10 MB

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

# Statuses a CSV invoice import may land an invoice at *directly* (issue #174).
# Only initial (`new`) and terminal-historical (`done`/`paid`/`rejected`) states
# are safe: they don't drop a fabricated invoice into a live, actionable pipeline
# stage. The mid-pipeline states — `approved` (payable NOW, no second approver),
# `ready_for_review`, `pending`, the ERP-send + payment_scheduled states, and
# `failed` — are blocked because reaching them by-passes the workflow engine,
# so `dispatch_audit`, `check_segregation`, and the approval signature never run.
# Open AP that still needs paying must be imported as `new` and go through the
# normal approval controls.
_IMPORTABLE_INVOICE_STATUSES = frozenset({"new", "done", "paid", "rejected"})

_CORPORATE_CARD_COLUMNS = {
    "external_txn_id",
    "date",
    "posted_date",
    "merchant",
    "amount",
    "currency",
    "card_last_four",
    "card_ref",
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
    """Parse a money cell, or ``None`` if it is unusable.

    Both call sites write the result into a ``Numeric(15, 2)`` column and turn a
    ``None`` into an ``ImportRowError``, so the magnitude check belongs here: an
    over-range cell used to parse fine and then raise
    ``NumericValueOutOfRangeError`` at the flush, aborting an import that had
    already written earlier rows. It is now one named bad row among the others.
    Scale is NOT enforced — Postgres rounds it, and refusing a migration row
    because the customer's old system carried a third decimal loses more than it
    protects (see ``services/numeric_bounds``).
    """
    if raw is None or raw == "":
        return None
    cleaned = raw.replace(",", "").replace("$", "").strip()
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return value if fits_numeric(value, *MONEY_NUMERIC) else None


def _parse_date(raw: str | None, *, day_first: bool = False) -> date | None:
    """Parse a CSV date cell. ISO and ``YYYY/MM/DD`` are unambiguous and tried
    first; the remaining ``M/D/Y`` vs ``D/M/Y`` case is genuinely ambiguous and
    is resolved by the caller-supplied ``day_first`` (the org's own configured
    locale signal — see ``app.utils.dates.resolve_day_first_preference``)
    via the shared ``parse_ambiguous_date`` helper, never a hardcoded order."""
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return parse_ambiguous_date(raw, day_first=day_first)


# ---------------------------------------------------------------------------
# Vendor import
# ---------------------------------------------------------------------------


async def import_vendors_csv(
    db: AsyncSession,
    organization_id: uuid.UUID,
    csv_text: str,
    entity_id: uuid.UUID | None = None,
) -> ImportResult:
    """Upsert vendors from CSV. Dedup priority: code > case-insensitive name.

    ``entity_id`` (multi-entity Phase 2) is the entity new vendors land under —
    the selected entity or the tenant default, resolved at the endpoint."""
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
            entity_id=entity_id,
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
    entity_id: uuid.UUID | None = None,
    day_first: bool = False,
) -> ImportResult:
    """Import historical invoices. Vendor resolution: code > name. Missing vendors
    get an auto-created stub with status='unverified' so the row still lands.

    ``entity_id`` (multi-entity Phase 2) is the entity imported invoices and any
    auto-created vendor stubs land under — the selected entity or the tenant
    default, resolved at the endpoint.

    ``day_first`` resolves ambiguous ``invoice_date`` / ``due_date`` cells
    (see ``app.utils.dates.resolve_day_first_preference``)."""
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
            entity_id=entity_id,
        )

        status_raw = (row.get("status") or "done").strip().lower()
        try:
            status_val = InvoiceStatus(status_raw)
        except ValueError:
            result.errors.append(ImportRowError(row=i, message=f"status invalid: {status_raw!r}"))
            continue
        # A CSV import bypasses the workflow engine (no audit / segregation /
        # approval signature), so it may only land at a safe initial/terminal
        # status — never a live pipeline stage like `approved` (issue #174).
        if status_raw not in _IMPORTABLE_INVOICE_STATUSES:
            result.errors.append(
                ImportRowError(
                    row=i,
                    message=(
                        f"status not importable: {status_raw!r}; "
                        f"allowed: {', '.join(sorted(_IMPORTABLE_INVOICE_STATUSES))} "
                        "(import open AP as 'new' so it goes through approval)"
                    ),
                )
            )
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
            entity_id=entity_id,
            invoice_number=invoice_number,
            vendor_name=vendor.name,
            vendor_id=vendor.id,
            amount=amount,
            currency=(row.get("currency") or "USD").upper()[:3],
            invoice_date=_parse_date(row.get("invoice_date"), day_first=day_first),
            due_date=_parse_date(row.get("due_date"), day_first=day_first),
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
    entity_id: uuid.UUID | None = None,
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
        entity_id=entity_id,
        name=vendor_name or (vendor_code or "Unknown Vendor"),
        code=vendor_code,
        status="unverified",
        source="manual",
    )
    db.add(vendor)
    await db.flush()
    return vendor


# ---------------------------------------------------------------------------
# Corporate-card transaction import (Expense Management WF4)
# ---------------------------------------------------------------------------


async def import_corporate_card_csv(
    db: AsyncSession,
    organization_id: uuid.UUID,
    csv_text: str,
    entity_id: uuid.UUID | None = None,
    import_batch: str | None = None,
    day_first: bool = False,
) -> ImportResult:
    """Import a corporate-card transaction feed from CSV into
    ``CorporateCardTransaction`` rows.

    Columns: ``external_txn_id``, ``date``, ``posted_date``, ``merchant``,
    ``amount``, ``currency``, ``card_last_four``, ``card_ref``. Column order
    does not matter; unknown columns are ignored.

    Idempotency: a row whose ``external_txn_id`` already exists for the org is
    skipped (counted in ``skipped``). The partial-unique index
    ``uq_corporate_card_txn_external`` backs this; the in-Python precheck (plus a
    per-file ``seen`` set, since the batch flushes once at the end) avoids the
    IntegrityError on the common path. ``import_batch`` stamps every imported row
    so a single upload can be reviewed / rolled back as a unit.

    PII: only ``card_last_four`` is stored (truncated to 4) — never a full PAN.
    The ``raw`` JSONB is filtered to the ``_CORPORATE_CARD_COLUMNS`` allowlist
    (with ``card_last_four`` masked) so an unrecognized PAN/account column in a
    real bank export is dropped rather than persisted.
    Callers commit the session themselves after inspecting the result."""
    from app.models.expense import CorporateCardTransaction, ReconciliationStatus

    result = ImportResult()
    try:
        rows = _read_rows(csv_text)
    except csv.Error as exc:
        result.errors.append(ImportRowError(row=0, message=f"Malformed CSV: {exc}"))
        return result

    seen: set[str] = set()
    for i, row in enumerate(rows, start=2):  # +1 header, +1 for 1-indexing
        external_txn_id = (row.get("external_txn_id") or "").strip() or None
        amount = _parse_decimal(row.get("amount"))
        txn_date = _parse_date(row.get("date"), day_first=day_first)

        # Negatives are DELIBERATELY allowed here, unlike the invoice importer:
        # a card refund / merchant credit / chargeback is a real negative line
        # on the feed, and `CorporateCardTransactionBase.amount` documents the
        # same call (no `ge=0`). `import-csv` is the only route that creates
        # feed rows, so rejecting them made the documented allowance
        # unreachable and every refund line in a bank export was refused — the
        # feed then no longer reconciles to the statement. Only an unparseable
        # amount is an error. A negative row never suggests a match (expenses
        # are non-negative) and is closed out via `/ignore`.
        if amount is None:
            result.errors.append(
                ImportRowError(row=i, message=f"amount invalid: {row.get('amount')!r}")
            )
            continue
        if txn_date is None:
            result.errors.append(
                ImportRowError(row=i, message=f"date invalid: {row.get('date')!r}")
            )
            continue

        # Dedupe (idempotency guard) — in-file first, then against the DB.
        if external_txn_id is not None:
            if external_txn_id in seen:
                result.skipped += 1
                continue
            existing = (
                await db.execute(
                    select(CorporateCardTransaction).where(
                        CorporateCardTransaction.organization_id == organization_id,
                        CorporateCardTransaction.external_txn_id == external_txn_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                result.skipped += 1
                continue
            seen.add(external_txn_id)

        last_four = (row.get("card_last_four") or "").strip()[:4] or None
        # Persist ONLY the known columns into `raw` — a real bank/card export can
        # carry a full PAN / account-number column, and the whole unfiltered row
        # would otherwise sit at rest in the tenant DB (PII/banking invariant,
        # issue #173). The masked last-4 replaces whatever the source put in the
        # card_last_four column, so no full PAN survives even there.
        safe_raw = {k: row[k] for k in _CORPORATE_CARD_COLUMNS if k in row}
        if "card_last_four" in safe_raw:
            safe_raw["card_last_four"] = last_four
        txn = CorporateCardTransaction(
            organization_id=organization_id,
            entity_id=entity_id,
            external_txn_id=external_txn_id,
            txn_date=txn_date,
            posted_date=_parse_date(row.get("posted_date"), day_first=day_first),
            merchant=(row.get("merchant") or None) or None,
            amount=amount,
            currency=(row.get("currency") or "USD").upper()[:3],
            card_last_four=last_four,
            card_ref=(row.get("card_ref") or None) or None,
            import_batch=import_batch,
            reconciliation_status=ReconciliationStatus.unmatched,
            raw=safe_raw,
        )
        db.add(txn)
        result.imported += 1

    await db.flush()
    return result
