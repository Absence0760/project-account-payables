"""CSV report exporters.

Each report is a pure function: takes a list of duck-typed rows
(SQLAlchemy ORM objects or test SimpleNamespace) and returns a CSV
string. The API layer pulls the rows + the right report function +
streams the response with `text/csv` + Content-Disposition.

Reports shipped today:
  - invoice_register: every invoice in a period with vendor, amount,
    status, dates
  - vendor_spend: per-vendor rollup (vendor, invoice_count, total)
  - payment_register: every payment with its invoice + status +
    fees
  - aging_snapshot: current/1-30/31-60/61-90/90+ buckets with totals
  - cashflow_forecast: projected AP outflows per period (day / week /
    month buckets) with committed vs pending split + discount-eligible

PDF is a separate concern (reportlab + pre-built templates); not
shipped here.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Iterable
from datetime import UTC, date, datetime
from decimal import Decimal

from app.services.branding import BrandContext


def brand_provenance_header(
    brand: BrandContext | None,
    *,
    org_name: str | None = None,
    report: str | None = None,
    generated_at: datetime | None = None,
) -> str:
    """Build a CSV provenance / brand header block.

    CSV has no visual chrome, so white-label branding here is a block of
    leading **comment lines** (each prefixed with ``# ``) carrying the tenant's
    product name, the org name, the report name, and the generation timestamp.
    A leading ``#``-comment block is the standard CSV-export provenance
    convention: the data grid (header row + rows) is **unchanged** and still
    parses column-positionally — a consumer that doesn't recognise comments
    skips the handful of leading ``#`` lines (csv.reader yields them as
    single-cell rows starting with ``#``; pandas takes ``comment="#"``).

    PII-free: only the product name + org name + report + timestamp — never a
    bank number, tax id, or address. Returns ``""`` when ``brand`` is ``None``
    so the pure per-report exporters stay byte-for-byte unchanged when no brand
    context is threaded through (back-compat for the column-shape tests + any
    caller that doesn't want a header).
    """
    if brand is None:
        return ""
    when = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"# {brand.product_name} — Analytics Export"]
    if org_name:
        lines.append(f"# Organization: {_sanitize_comment(org_name)}")
    if report:
        lines.append(f"# Report: {_sanitize_comment(report)}")
    lines.append(f"# Generated: {when}")
    # Each comment line is its own physical CSV line; the data grid follows.
    return "\r\n".join(lines) + "\r\n"


def _sanitize_comment(value: str) -> str:
    """Keep a comment line single-line — strip CR/LF so an org name with a
    newline can't inject a fake data row. (Product name + report are
    code/schema-controlled; org name is the only tenant-supplied field.)"""
    return value.replace("\r", " ").replace("\n", " ").strip()


def _writer(headers: list[str]) -> tuple[io.StringIO, csv.writer]:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    w.writerow(headers)
    return buf, w


def _fmt_money(x) -> str:
    if x is None:
        return ""
    return f"{Decimal(str(x)).quantize(Decimal('0.01'))}"


def _fmt_date(x) -> str:
    if x is None:
        return ""
    if hasattr(x, "isoformat"):
        return x.isoformat()
    return str(x)


def export_invoice_register(invoices: Iterable) -> str:
    """One row per Invoice. Includes amount, currency, status,
    vendor, dates (invoice / due / created), and the invoice
    number / id pair so a finance team can re-import upstream."""
    buf, w = _writer(
        [
            "invoice_id",
            "invoice_number",
            "vendor_name",
            "amount",
            "currency",
            "status",
            "invoice_date",
            "due_date",
            "created_at",
            "po_number",
        ]
    )
    for inv in invoices:
        status = getattr(inv, "status", None)
        if hasattr(status, "value"):
            status = status.value
        w.writerow(
            [
                str(getattr(inv, "id", "")),
                getattr(inv, "invoice_number", "") or "",
                getattr(inv, "vendor_name", "") or "",
                _fmt_money(getattr(inv, "amount", None)),
                getattr(inv, "currency", "") or "USD",
                status or "",
                _fmt_date(getattr(inv, "invoice_date", None)),
                _fmt_date(getattr(inv, "due_date", None)),
                _fmt_date(getattr(inv, "created_at", None)),
                getattr(inv, "po_number", "") or "",
            ]
        )
    return buf.getvalue()


def export_vendor_spend(rows: Iterable) -> str:
    """Per-vendor rollup: name, invoice_count, total. The caller is
    responsible for the SQL aggregation; this function only knows
    how to serialise the resulting rows. Each row is a 3-tuple
    `(vendor_name, invoice_count, total)` OR an object with the
    same attributes."""
    buf, w = _writer(["vendor_name", "invoice_count", "total_amount"])
    for r in rows:
        if isinstance(r, (tuple, list)) and len(r) >= 3:
            vendor, count, total = r[0], r[1], r[2]
        else:
            vendor = getattr(r, "vendor_name", None) or getattr(r, "vendor", None)
            count = getattr(r, "invoice_count", 0)
            total = getattr(r, "total_amount", None) or getattr(r, "amount", None)
        w.writerow([vendor or "", int(count or 0), _fmt_money(total)])
    return buf.getvalue()


def export_payment_register(payments_with_invoice: Iterable) -> str:
    """Each row is a `(Payment, Invoice)` pair (the SQL layer joins
    them). For payments whose invoice was deleted / orphaned, the
    second slot may be None — we emit the payment row with the
    invoice columns blank rather than skipping (the finance team
    still wants to see the money out)."""
    buf, w = _writer(
        [
            "payment_id",
            "invoice_id",
            "invoice_number",
            "vendor_name",
            "amount",
            "currency",
            "method",
            "status",
            "provider",
            "reference",
            "submitted_at",
            "completed_at",
        ]
    )
    for pair in payments_with_invoice:
        if isinstance(pair, (tuple, list)):
            payment, invoice = pair[0], pair[1] if len(pair) > 1 else None
        else:
            payment = pair
            invoice = getattr(pair, "_invoice", None)
        w.writerow(
            [
                str(getattr(payment, "id", "")),
                str(getattr(payment, "invoice_id", "")),
                getattr(invoice, "invoice_number", "") if invoice else "",
                getattr(invoice, "vendor_name", "") if invoice else "",
                _fmt_money(getattr(payment, "amount", None)),
                getattr(invoice, "currency", "USD") if invoice else "USD",
                getattr(payment, "method", "") or "",
                getattr(payment, "status", "") or "",
                getattr(payment, "provider", "") or "",
                getattr(payment, "reference", "") or "",
                _fmt_date(getattr(payment, "submitted_at", None)),
                _fmt_date(getattr(payment, "completed_at", None)),
            ]
        )
    return buf.getvalue()


def export_aging_snapshot(aging_buckets: dict, *, snapshot_date: date | None = None) -> str:
    """Single-row report with the as-of-date and the five buckets
    (current / 1-30 / 31-60 / 61-90 / 90+ days past due).
    Header matches the dashboard `aging` dict keys."""
    buf, w = _writer(
        ["as_of_date", "current", "days_30", "days_60", "days_90", "days_90_plus", "total"]
    )
    current = Decimal(str(aging_buckets.get("current", 0) or 0))
    d30 = Decimal(str(aging_buckets.get("days_30", 0) or 0))
    d60 = Decimal(str(aging_buckets.get("days_60", 0) or 0))
    d90 = Decimal(str(aging_buckets.get("days_90", 0) or 0))
    d90plus = Decimal(str(aging_buckets.get("days_90_plus", 0) or 0))
    total = current + d30 + d60 + d90 + d90plus
    w.writerow(
        [
            _fmt_date(snapshot_date or date.today()),
            _fmt_money(current),
            _fmt_money(d30),
            _fmt_money(d60),
            _fmt_money(d90),
            _fmt_money(d90plus),
            _fmt_money(total),
        ]
    )
    return buf.getvalue()


def export_cashflow_forecast(period_rows: Iterable) -> str:
    """One row per forecast period. Each row is a dict as produced by
    `analytics.bucket_outflows` — period key, start/end bounds, the
    scheduled total, the committed-vs-pending split, the
    discount-eligible amount, and the invoice count. The CFO drops this
    straight into their FP&A model."""
    buf, w = _writer(
        [
            "period",
            "period_start",
            "period_end",
            "scheduled_amount",
            "committed_amount",
            "pending_amount",
            "discount_eligible_amount",
            "count",
        ]
    )
    for p in period_rows:
        w.writerow(
            [
                p.get("period", "") or "",
                _fmt_date(p.get("period_start")),
                _fmt_date(p.get("period_end")),
                _fmt_money(p.get("scheduled_amount")),
                _fmt_money(p.get("committed_amount")),
                _fmt_money(p.get("pending_amount")),
                _fmt_money(p.get("discount_eligible_amount")),
                int(p.get("count", 0) or 0),
            ]
        )
    return buf.getvalue()


def export_expense_register(rows: Iterable) -> str:
    """One row per Expense — the expense register a finance team reconciles or
    re-imports. Each row is a ``(Expense, report_number, gl_code)`` tuple: the
    route does the joins (``GLAccount`` for the code, ``ExpenseReport`` for the
    number) so this stays a pure serializer (mirrors ``export_payment_register``
    pairing). ``status`` / ``payment_method`` are ``StrEnum`` so ``str(...)``
    already yields the plain value; the ``.value`` guard is belt-and-suspenders."""
    buf, w = _writer(
        [
            "date",
            "merchant",
            "category",
            "amount",
            "currency",
            "gl_code",
            "payment_method",
            "status",
            "report_number",
        ]
    )
    for row in rows:
        # `row` is a (Expense, report_number, gl_code) sequence — a SQLAlchemy
        # `Row`, a plain tuple/list, or (test convenience) a bare Expense-like
        # object carrying its own `expense_date` plus the two extras as attrs.
        if hasattr(row, "expense_date"):
            e = row
            report_number = getattr(row, "report_number", None)
            gl_code = getattr(row, "gl_code", None)
        else:
            seq = list(row)
            e = seq[0]
            report_number = seq[1] if len(seq) > 1 else None
            gl_code = seq[2] if len(seq) > 2 else None
        status = getattr(e, "status", None)
        if hasattr(status, "value"):
            status = status.value
        payment_method = getattr(e, "payment_method", None)
        if hasattr(payment_method, "value"):
            payment_method = payment_method.value
        w.writerow(
            [
                _fmt_date(getattr(e, "expense_date", None)),
                getattr(e, "merchant", "") or "",
                getattr(e, "category", "") or "",
                _fmt_money(getattr(e, "amount", None)),
                getattr(e, "currency", "") or "USD",
                gl_code or "",
                payment_method or "",
                status or "",
                report_number or "",
            ]
        )
    return buf.getvalue()


# Registry — keep one in-process so the API layer can do
# `EXPORTERS["invoice_register"]` and not hand-route per name.
EXPORTERS: dict[str, Callable] = {
    "invoice_register": export_invoice_register,
    "vendor_spend": export_vendor_spend,
    "payment_register": export_payment_register,
    "aging_snapshot": export_aging_snapshot,
    "cashflow_forecast": export_cashflow_forecast,
    "expense_register": export_expense_register,
}
