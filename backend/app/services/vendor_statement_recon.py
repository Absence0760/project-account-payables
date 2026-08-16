"""Vendor statement reconciliation — the pure matching engine.

A supplier periodically sends a **statement of open items**: every invoice it
believes we still owe, as of a `statement_date`. Reconciling that statement
against our own AP ledger is a core month-end-close task. This module is the
pure engine: it parses the supplier's CSV into :class:`StatementLine`s and
matches those against our :class:`LedgerInvoice`s, classifying each line into
one of the four reconciliation outcomes the ORM models define
(``app.models.vendor_statement_recon``):

  * ``matched``               — statement line ↔ our invoice, amounts agree
  * ``amount_mismatch``       — same invoice, amounts differ beyond tolerance
  * ``missing_on_our_side``   — supplier billed it, we have no invoice
  * ``missing_on_their_side`` — we have an open invoice the statement omitted

The module is **pure**: no DB session, no I/O, no network, no ``async``. It
operates on dataclasses only, and every money value is :class:`~decimal.Decimal`
(never float). The local CSV-parsing helpers (`_find_col`, `parse_date`,
`parse_amount`) mirror the forgiving idioms in
``app.services.bank_reconciliation`` but are reimplemented here so the engine
stays self-contained.

See ``backend/docs/vendor-statement-reconciliation.md``.
"""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.models.vendor_statement_recon import (
    CLASS_AMOUNT_MISMATCH,
    CLASS_MATCHED,
    CLASS_MISSING_OUR_SIDE,
    CLASS_MISSING_THEIR_SIDE,
)

# Matching tolerances — overridable per call.
DEFAULT_AMOUNT_TOLERANCE = Decimal("0.01")
DEFAULT_DATE_WINDOW_DAYS = 5

_ZERO = Decimal("0")


class StatementParseError(ValueError):
    """Raised when a statement CSV can't be parsed at the structural level —
    no header, no data rows, or neither an invoice-number nor an amount column.
    The caller (`/api/vendor-statements`) turns this into a 422 with the
    message attached."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class StatementLine:
    """One parsed supplier-statement line (the supplier's view)."""

    invoice_number: str | None
    invoice_date: date | None
    amount: Decimal | None
    status: str | None
    raw: dict = field(default_factory=dict)


@dataclass
class LedgerInvoice:
    """One of OUR open invoices, projected into a minimal shape the engine
    needs — built by the router from `Invoice` rows."""

    id: uuid.UUID
    invoice_number: str
    amount: Decimal
    invoice_date: date | None
    currency: str
    status: str


@dataclass
class ReconLineResult:
    """The engine's per-line verdict — maps onto `VendorStatementReconLine`."""

    classification: str
    statement_invoice_number: str | None
    statement_date: date | None
    statement_amount: Decimal | None
    statement_status: str | None
    matched_invoice_id: uuid.UUID | None
    ledger_amount: Decimal | None
    amount_difference: Decimal | None
    match_method: str | None
    raw: dict | None


@dataclass
class ReconSummary:
    """The denormalised outcome rollup — maps onto the run's count/total
    columns."""

    line_count: int
    matched_count: int
    amount_mismatch_count: int
    missing_our_side_count: int
    missing_their_side_count: int
    statement_total: Decimal
    ledger_total: Decimal


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def normalize_invoice_number(value: str | None) -> str:
    """Canonicalise an invoice number for matching: strip, upper-case, and
    drop every non-alphanumeric character. ``None`` / blank → ``""``.

    So ``"INV-001"``, ``"inv 001"`` and ``"#INV001"`` all collapse to
    ``"INV001"`` and match each other.
    """
    if not value:
        return ""
    return "".join(c for c in value.upper() if c.isalnum())


# ---------------------------------------------------------------------------
# CSV parsing helpers (local copies of bank_reconciliation idioms)
# ---------------------------------------------------------------------------

# Column-header synonyms — case + whitespace insensitive.
_INVOICE_HEADERS = {
    "invoice",
    "invoice number",
    "invoice_number",
    "invoice no",
    "invoice #",
    "number",
    "ref",
    "reference",
    "document",
    "document number",
}
_DATE_HEADERS = {
    "date",
    "invoice date",
    "invoice_date",
    "document date",
    "due date",
}
_AMOUNT_HEADERS = {
    "amount",
    "balance",
    "open balance",
    "outstanding",
    "amount due",
    "total",
}
_STATUS_HEADERS = {"status", "state"}


def _find_col(headers: list[str], candidates: set[str]) -> str | None:
    """Return the first header that matches any candidate (case + whitespace
    insensitive), else ``None``."""
    norm = {h.strip().lower(): h for h in headers}
    for cand in candidates:
        if cand in norm:
            return norm[cand]
    return None


def parse_date(raw: str | None) -> date | None:
    """Accept ISO, MM/DD/YYYY, DD/MM/YYYY, YYYY/MM/DD. Returns ``None`` on a
    blank or unrecognised value (never raises).

    Public because the PDF intake path (``vendor_statement_extraction``)
    normalises an extraction adapter's raw date STRING with the same parser the
    CSV path uses — one statement date format story, not two."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_amount(raw: str | None) -> Decimal | None:
    """Accept ``1234.56``, ``1,234.56``, ``(1,234.56)`` (negative), ``$``,
    ``-1234.56``. Returns the signed Decimal, or ``None`` on a blank /
    unparseable value (e.g. ``"-"``).

    Public for the same reason as :func:`parse_date`: the PDF intake path turns
    an adapter's raw amount STRING into money here, so a model's output and a
    CSV cell become a ``Decimal`` by exactly the same rules — and neither ever
    passes through a float."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("$", "").strip()
    if s.startswith("-"):
        negative = True
        s = s[1:].strip()
    if not s:
        return None
    try:
        amount = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


def parse_statement_csv(raw_csv: bytes) -> list[StatementLine]:
    """Parse a supplier-statement CSV into a list of :class:`StatementLine`.

    Forgiving: decodes ``utf-8-sig`` (BOM-tolerant) and falls back to
    ``latin-1``; sniffs the header row and accepts common column synonyms for
    the invoice-number / date / amount / status fields.

    Structural failures raise :class:`StatementParseError`:
      * empty body or fewer than two rows (header + ≥1 data row);
      * header carrying neither an invoice-number column nor an amount column.

    A data row with an unparseable amount **and** no invoice number is skipped
    (it carries nothing to match on); otherwise the row is kept with whatever
    parsed (``amount`` may be ``None``).
    """
    try:
        text = raw_csv.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw_csv.decode("latin-1", errors="replace")

    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows) < 2:
        raise StatementParseError("CSV is empty or has no data rows")

    headers = [h.strip() for h in rows[0]]
    invoice_col = _find_col(headers, _INVOICE_HEADERS)
    date_col = _find_col(headers, _DATE_HEADERS)
    amount_col = _find_col(headers, _AMOUNT_HEADERS)
    status_col = _find_col(headers, _STATUS_HEADERS)

    if not invoice_col and not amount_col:
        raise StatementParseError(
            "CSV header is missing both an invoice-number column "
            f"(expected one of {sorted(_INVOICE_HEADERS)}) and an amount column "
            f"(expected one of {sorted(_AMOUNT_HEADERS)})"
        )

    idx = {h: i for i, h in enumerate(headers)}
    lines: list[StatementLine] = []

    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue  # blank line
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))

        invoice_number = row[idx[invoice_col]].strip() if invoice_col else ""
        invoice_number = invoice_number or None
        statement_date = parse_date(row[idx[date_col]]) if date_col else None
        amount = parse_amount(row[idx[amount_col]]) if amount_col else None
        status = row[idx[status_col]].strip() if status_col else None
        status = status or None

        # A row that carries nothing to match on (no number AND no usable
        # amount) is noise — skip it.
        if amount is None and not invoice_number:
            continue

        raw = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}
        lines.append(
            StatementLine(
                invoice_number=invoice_number,
                invoice_date=statement_date,
                amount=amount,
                status=status,
                raw=raw,
            )
        )

    return lines


# ---------------------------------------------------------------------------
# Reconciliation engine
# ---------------------------------------------------------------------------


def _dates_within_window(a: date | None, b: date | None, window_days: int) -> bool:
    """Amount-equality already qualifies the candidate; the date window only
    narrows it WHEN both dates are present. A missing date passes (we don't
    have the signal to reject on)."""
    if a is None or b is None:
        return True
    return abs((a - b).days) <= window_days


def reconcile(
    statement_lines: list[StatementLine],
    ledger_invoices: list[LedgerInvoice],
    *,
    amount_tolerance: Decimal = DEFAULT_AMOUNT_TOLERANCE,
    date_window_days: int = DEFAULT_DATE_WINDOW_DAYS,
) -> tuple[list[ReconLineResult], ReconSummary]:
    """Reconcile a supplier statement against our ledger.

    Deterministic and stable-ordered: statement lines are processed in input
    order; each ledger invoice is consumed by at most one statement line.
    Matching is by normalised invoice number first, then an amount-equal /
    date-window fallback. After the statement lines, every unconsumed ledger
    invoice yields one ``missing_on_their_side`` result.

    Returns ``(results, summary)``.
    """
    consumed: set[uuid.UUID] = set()
    results: list[ReconLineResult] = []

    # Pre-index ledger invoices by normalised number for the exact-number leg.
    # First-wins on a number collision, mirroring the linear scan below.
    by_number: dict[str, list[LedgerInvoice]] = {}
    for inv in ledger_invoices:
        by_number.setdefault(normalize_invoice_number(inv.invoice_number), []).append(inv)

    for line in statement_lines:
        match: LedgerInvoice | None = None
        method: str | None = None

        # Leg 1: exact normalised invoice-number match against an unconsumed
        # ledger invoice.
        key = normalize_invoice_number(line.invoice_number)
        if key:
            for inv in by_number.get(key, []):
                if inv.id not in consumed:
                    match = inv
                    method = "invoice_number"
                    break

        # Leg 2: amount-equal + date-window fallback.
        if match is None and line.amount is not None:
            for inv in ledger_invoices:
                if inv.id in consumed:
                    continue
                if inv.amount == line.amount and _dates_within_window(
                    line.invoice_date, inv.invoice_date, date_window_days
                ):
                    match = inv
                    method = "amount_date"
                    break

        if match is None:
            results.append(
                ReconLineResult(
                    classification=CLASS_MISSING_OUR_SIDE,
                    statement_invoice_number=line.invoice_number,
                    statement_date=line.invoice_date,
                    statement_amount=line.amount,
                    statement_status=line.status,
                    matched_invoice_id=None,
                    ledger_amount=None,
                    amount_difference=None,
                    match_method=None,
                    raw=line.raw,
                )
            )
            continue

        consumed.add(match.id)
        statement_amount = line.amount if line.amount is not None else _ZERO
        amount_difference = statement_amount - match.amount
        classification = (
            CLASS_MATCHED if abs(amount_difference) <= amount_tolerance else CLASS_AMOUNT_MISMATCH
        )
        results.append(
            ReconLineResult(
                classification=classification,
                statement_invoice_number=line.invoice_number,
                statement_date=line.invoice_date,
                statement_amount=line.amount,
                statement_status=line.status,
                matched_invoice_id=match.id,
                ledger_amount=match.amount,
                amount_difference=amount_difference,
                match_method=method,
                raw=line.raw,
            )
        )

    # Every unconsumed ledger invoice → one missing_on_their_side row.
    for inv in ledger_invoices:
        if inv.id in consumed:
            continue
        results.append(
            ReconLineResult(
                classification=CLASS_MISSING_THEIR_SIDE,
                statement_invoice_number=None,
                statement_date=None,
                statement_amount=None,
                statement_status=None,
                matched_invoice_id=inv.id,
                ledger_amount=inv.amount,
                amount_difference=None,
                match_method=None,
                raw=None,
            )
        )

    summary = _build_summary(results)
    return results, summary


def _build_summary(results: list[ReconLineResult]) -> ReconSummary:
    matched = amount_mismatch = missing_our = missing_their = 0
    statement_total = _ZERO
    ledger_total = _ZERO
    for r in results:
        if r.classification == CLASS_MATCHED:
            matched += 1
        elif r.classification == CLASS_AMOUNT_MISMATCH:
            amount_mismatch += 1
        elif r.classification == CLASS_MISSING_OUR_SIDE:
            missing_our += 1
        elif r.classification == CLASS_MISSING_THEIR_SIDE:
            missing_their += 1

        # statement_total: every statement-origin line (i.e. not the
        # their-side orphans, which have no statement amount).
        if r.classification != CLASS_MISSING_THEIR_SIDE and r.statement_amount is not None:
            statement_total += r.statement_amount

        # ledger_total: the ledger amount of every invoice we matched.
        if (
            r.classification in (CLASS_MATCHED, CLASS_AMOUNT_MISMATCH)
            and r.ledger_amount is not None
        ):
            ledger_total += r.ledger_amount

    return ReconSummary(
        line_count=len(results),
        matched_count=matched,
        amount_mismatch_count=amount_mismatch,
        missing_our_side_count=missing_our,
        missing_their_side_count=missing_their,
        statement_total=statement_total,
        ledger_total=ledger_total,
    )


def line_unreconciled_amount(
    classification: str,
    statement_amount: Decimal | None,
    amount_difference: Decimal | None,
) -> Decimal:
    """The materiality contribution of one reconciliation line — the unresolved
    money it represents toward a close-readiness threshold:

      * ``missing_on_our_side``  → ``abs(statement_amount)`` (we owe it, untracked)
      * ``amount_mismatch``      → ``abs(amount_difference)`` (the gap to resolve)
      * everything else          → ``0``

    Never raises on ``None`` inputs.
    """
    if classification == CLASS_MISSING_OUR_SIDE:
        return abs(statement_amount) if statement_amount is not None else _ZERO
    if classification == CLASS_AMOUNT_MISMATCH:
        return abs(amount_difference) if amount_difference is not None else _ZERO
    return _ZERO
