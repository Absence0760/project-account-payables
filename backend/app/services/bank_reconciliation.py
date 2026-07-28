"""Bank statement import + reconciliation matcher.

Two responsibilities:

  1. **Import** — parse a CSV bank-statement export into
     `BankStatement` + `BankTransaction` rows. CSV is the only
     format we ship today; OFX and camt.053 hook in via the
     `source_format` parameter and a separate parser, deferred.

  2. **Match** — for every debit transaction, find the Payment row
     it corresponds to. Three strategies in order:
       a. **provider_id** — exact match of the transaction
          `reference` against `Payment.provider_payment_id` or
          `Payment.reference`. Confidence 100.
       b. **amount + date** — same amount within a ±N-day window
          of `Payment.submitted_at`. Confidence 80.
       c. **fuzzy vendor** — same amount within window + the
          transaction's `counterparty_name` matches the invoice's
          `vendor_name` via the same fuzzy logic vendor matching
          uses (token Jaccard). Confidence is the Jaccard score
          scaled into 50–70.

A transaction with NO match stays `matched_payment_id=NULL`. Today
the AP team reviews unmatched transactions from the statement detail
view (`GET /api/bank-reconciliation/{id}`) and resolves them by hand
via `POST .../transactions/{id}/resolve` — there is no automatic
`Exception` row yet (unlike `invoice_warnings.py`'s duplicate/fraud
checks). Wiring an `unmatched_bank_transaction` exception type into
the queue is tracked as follow-up work; see
`docs/bank-reconciliation.md` § Deferred.
"""

from __future__ import annotations

import csv
import io
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bank_reconciliation import BankStatement, BankTransaction
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.services.vendor_matching import _normalize, _similarity

logger = logging.getLogger(__name__)


class StatementImportError(ValueError):
    """Raised when a CSV statement can't be parsed at the structural
    level — missing required columns, unrecognised header, empty body.
    The caller turns this into a 422 with the message attached."""


@dataclass
class _ParsedRow:
    transaction_date: date
    posted_date: date | None
    amount: Decimal
    direction: str  # 'debit' | 'credit'
    description: str | None
    counterparty_name: str | None
    reference: str | None
    raw: dict


# Default match window (in days) for the amount+date strategy. The
# bank's posted date can lag the originator's submitted_at by 1–3
# business days on ACH; wires and RTP settle same-day or T+1.
_DEFAULT_MATCH_WINDOW_DAYS = 5
# Confidence thresholds — bands documented above.
_CONFIDENCE_PROVIDER_ID = Decimal("100.00")
_CONFIDENCE_AMOUNT_DATE = Decimal("80.00")
_FUZZY_MIN_JACCARD = 0.5  # below this we don't even try
_FUZZY_CONFIDENCE_BASE = Decimal("50.00")
_FUZZY_CONFIDENCE_SPREAD = Decimal("20.00")


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------

# Most retail banks export columns with these labels. We accept any
# of the synonyms below — case-insensitive, whitespace-stripped.
_DATE_HEADERS = {"date", "transaction date", "transaction_date", "posted date", "post date"}
_AMOUNT_HEADERS = {"amount", "transaction amount", "amount (usd)", "value"}
_DEBIT_HEADERS = {"debit", "withdrawal", "withdrawals"}
_CREDIT_HEADERS = {"credit", "deposit", "deposits"}
_DESC_HEADERS = {"description", "memo", "details", "note", "narrative"}
_REFERENCE_HEADERS = {"reference", "ref", "trace number", "trace", "check number"}
_COUNTERPARTY_HEADERS = {"counterparty", "payee", "name", "merchant", "vendor"}


def _find_col(headers: list[str], candidates: set[str]) -> str | None:
    """Return the first header in `headers` that matches any of
    `candidates` (case + whitespace insensitive)."""
    norm = {h.strip().lower(): h for h in headers}
    for cand in candidates:
        if cand in norm:
            return norm[cand]
    return None


def _parse_date(raw: str) -> date | None:
    """Accept common bank date formats: ISO, MM/DD/YYYY, DD/MM/YYYY.

    We try ISO first because it's unambiguous; then US-style
    MM/DD/YYYY (most US bank exports); then DMY as a last resort.
    A truly ambiguous date like 01/02/2026 will be read as Jan 2 —
    the parser can't know better. CSV-driven imports should use
    ISO when possible.
    """
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(raw: str) -> Decimal | None:
    """Accept `1234.56`, `1,234.56`, `(1,234.56)` (negative form),
    `-1234.56`. Returns the absolute Decimal; sign is handled by
    the debit/credit logic separately."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "").replace("$", "").strip()
    if s.startswith("-"):
        negative = True
        s = s[1:]
    try:
        amount = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


def parse_csv_statement(
    *,
    raw_csv: bytes,
    organization_id: uuid.UUID,
    account_identifier: str,
    period_start: date,
    period_end: date,
    currency: str = "USD",
    imported_by: uuid.UUID | None = None,
    file_key: str | None = None,
) -> tuple[BankStatement, list[BankTransaction]]:
    """Parse a CSV bank-statement export into a Statement + list of
    Transaction rows. The transactions are NOT yet matched — call
    `match_statement_transactions` after persisting.

    The parser is forgiving: it sniffs the header row, accepts
    common column-name synonyms, handles amount sign via both
    debit/credit columns AND signed-amount columns. A row that's
    structurally bad (unparseable date, unparseable amount) is
    skipped silently with a WARNING; structural errors at the
    statement level (no header, no rows) raise.
    """
    try:
        text = raw_csv.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Some bank exports use latin-1 / cp1252. Fall back to
        # latin-1 — it never errors and produces something
        # human-reviewable.
        text = raw_csv.decode("latin-1", errors="replace")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows or len(rows) < 2:
        raise StatementImportError("CSV is empty or has no data rows")

    headers = [h.strip() for h in rows[0]]
    date_col = _find_col(headers, _DATE_HEADERS)
    amount_col = _find_col(headers, _AMOUNT_HEADERS)
    debit_col = _find_col(headers, _DEBIT_HEADERS)
    credit_col = _find_col(headers, _CREDIT_HEADERS)
    desc_col = _find_col(headers, _DESC_HEADERS)
    ref_col = _find_col(headers, _REFERENCE_HEADERS)
    cp_col = _find_col(headers, _COUNTERPARTY_HEADERS)

    if not date_col:
        raise StatementImportError(
            f"CSV header is missing a date column (expected one of {sorted(_DATE_HEADERS)})"
        )
    if not amount_col and not (debit_col or credit_col):
        raise StatementImportError(
            "CSV header is missing both an amount column and debit/credit columns"
        )

    header_to_idx = {h: i for i, h in enumerate(headers)}
    parsed: list[_ParsedRow] = []

    for row_no, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue  # blank line
        if len(row) < len(headers):
            row = row + [""] * (len(headers) - len(row))
        d = _parse_date(row[header_to_idx[date_col]])
        if d is None:
            logger.warning("[bank_reconciliation] row %d: bad date, skipping", row_no)
            continue

        amount: Decimal | None = None
        direction = "debit"
        if amount_col:
            amount = _parse_amount(row[header_to_idx[amount_col]])
            if amount is None:
                logger.warning("[bank_reconciliation] row %d: bad amount, skipping", row_no)
                continue
            direction = "debit" if amount < 0 else "credit"
            amount = abs(amount)
        else:
            # Separate debit/credit columns. Pick whichever is set.
            debit_val = _parse_amount(row[header_to_idx[debit_col]]) if debit_col else None
            credit_val = _parse_amount(row[header_to_idx[credit_col]]) if credit_col else None
            if debit_val is not None and debit_val != 0:
                amount = abs(debit_val)
                direction = "debit"
            elif credit_val is not None and credit_val != 0:
                amount = abs(credit_val)
                direction = "credit"
            else:
                continue  # both blank — skip

        description = row[header_to_idx[desc_col]].strip() if desc_col else None
        reference = row[header_to_idx[ref_col]].strip() if ref_col else None
        counterparty = row[header_to_idx[cp_col]].strip() if cp_col else None
        raw = {headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))}

        parsed.append(
            _ParsedRow(
                transaction_date=d,
                posted_date=None,
                amount=amount,
                direction=direction,
                description=description or None,
                counterparty_name=counterparty or None,
                reference=reference or None,
                raw=raw,
            )
        )

    if not parsed:
        raise StatementImportError("CSV had a header but no parseable transactions")

    statement = BankStatement(
        organization_id=organization_id,
        account_identifier=account_identifier,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        source_format="csv",
        file_key=file_key,
        imported_by=imported_by,
        transaction_count=len(parsed),
        matched_count=0,
    )

    transactions = [
        BankTransaction(
            organization_id=organization_id,
            transaction_date=p.transaction_date,
            posted_date=p.posted_date,
            amount=p.amount,
            currency=currency,
            description=p.description,
            counterparty_name=p.counterparty_name,
            reference=p.reference,
            direction=p.direction,
            raw_data=p.raw,
        )
        for p in parsed
    ]

    return statement, transactions


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchAttempt:
    """One try at matching a transaction to a payment. Used by
    `match_statement_transactions` to record the best attempt
    per transaction so a re-run can build on partial progress."""

    payment_id: uuid.UUID
    method: str
    confidence: Decimal


async def _candidate_payments_in_window(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    amount: Decimal,
    transaction_date: date,
    window_days: int,
) -> list[Payment]:
    """Pull payments whose amount equals the transaction's amount and
    whose `submitted_at` (fallback: `completed_at`, fallback:
    `created_at`) falls within `window_days` of `transaction_date`.
    The window check is done in Python — a SQL `BETWEEN` against a
    nullable timestamp set ends up clumsy across SQLite (tests) and
    Postgres (prod)."""
    floor = transaction_date - timedelta(days=window_days)
    ceiling = transaction_date + timedelta(days=window_days)
    result = await db.execute(
        select(Payment).where(
            and_(
                Payment.amount == amount,
                # Restrict by org via the invoice join — the
                # Payment.organization_id column doesn't exist
                # directly; payments are tenant-scoped via the DB.
                # In a tenant DB, every Payment row belongs to the
                # org by construction.
            )
        )
    )
    payments = result.scalars().all()
    out: list[Payment] = []
    for p in payments:
        ts = p.submitted_at or p.completed_at or p.created_at  # type: ignore[attr-defined]
        if ts is None:
            continue
        if isinstance(ts, datetime):
            ts_date = ts.date()
        else:
            ts_date = ts
        if floor <= ts_date <= ceiling:
            out.append(p)
    return out


async def _payment_by_reference(
    db: AsyncSession,
    reference: str,
) -> Payment | None:
    """Exact lookup against `provider_payment_id` first, then
    `reference`. The two columns store different but overlapping
    things — provider_payment_id is always the processor's ID;
    reference is whatever the processor returned (ACH trace number,
    check number, etc.)."""
    result = await db.execute(select(Payment).where(Payment.provider_payment_id == reference))
    found = result.scalar_one_or_none()
    if found is not None:
        return found
    result = await db.execute(select(Payment).where(Payment.reference == reference))
    return result.scalar_one_or_none()


async def _fuzzy_vendor_match(
    db: AsyncSession,
    *,
    candidates: list[Payment],
    counterparty_name: str,
) -> tuple[Payment, Decimal] | None:
    """Of the amount+date-window candidates, pick the one whose
    invoice's `vendor_name` best matches the bank transaction's
    `counterparty_name`. Returns (payment, confidence) or None when
    no candidate scores above the floor."""
    if not candidates or not counterparty_name:
        return None
    cp_key = _normalize(counterparty_name)
    if not cp_key:
        return None

    # Fetch the candidate invoices in one query.
    invoice_ids = [c.invoice_id for c in candidates]
    inv_rows = (
        (await db.execute(select(Invoice).where(Invoice.id.in_(invoice_ids)))).scalars().all()
    )
    by_id = {inv.id: inv for inv in inv_rows}

    best: tuple[Payment, Decimal] | None = None
    for payment in candidates:
        inv = by_id.get(payment.invoice_id)
        if inv is None or not inv.vendor_name:
            continue
        score = _similarity(cp_key, _normalize(inv.vendor_name))
        if score < _FUZZY_MIN_JACCARD:
            continue
        # Map jaccard 0.5–1.0 → confidence 50–70.
        confidence = (
            _FUZZY_CONFIDENCE_BASE
            + Decimal(str((score - _FUZZY_MIN_JACCARD) / (1.0 - _FUZZY_MIN_JACCARD)))
            * _FUZZY_CONFIDENCE_SPREAD
        )
        confidence = confidence.quantize(Decimal("0.01"))
        if best is None or confidence > best[1]:
            best = (payment, confidence)
    return best


async def match_statement_transactions(
    db: AsyncSession,
    transactions: Iterable[BankTransaction],
    *,
    window_days: int = _DEFAULT_MATCH_WINDOW_DAYS,
) -> dict[str, int]:
    """Walk the transactions and try to match each `debit` against a
    Payment row. Mutates each transaction in place, setting
    `matched_payment_id`, `match_method`, `match_confidence`,
    `matched_at`. Credits are skipped (we don't track incoming).

    A Payment can be matched to at most one BankTransaction — two bank
    lines can't both be "the" clearing of a single payment. Without this
    guard, two same-amount transactions inside the same window (a common
    shape: two invoices happen to be paid for the same amount) would each
    independently see that one Payment as their sole candidate and both
    claim it, silently double-counting it as reconciled while leaving
    whichever transaction actually belongs to a different (unrecorded or
    not-yet-imported) payment mismatched. `claimed` seeds from every
    Payment a PRIOR statement import already matched (so re-running this
    on a new statement can't re-claim one), then grows as this batch makes
    its own matches so two transactions in the same call can't collide
    either.

    Returns counts by outcome so the caller can summarise the result
    on the import API response: `{"matched": N, "unmatched": M,
    "skipped_credit": K}`.
    """
    counts = {"matched": 0, "unmatched": 0, "skipped_credit": 0}

    claimed: set[uuid.UUID] = set(
        (
            await db.execute(
                select(BankTransaction.matched_payment_id).where(
                    BankTransaction.matched_payment_id.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )

    for tx in transactions:
        if tx.direction != "debit":
            counts["skipped_credit"] += 1
            continue

        attempt: MatchAttempt | None = None

        # Strategy 1: exact reference match.
        if tx.reference:
            found = await _payment_by_reference(db, tx.reference)
            if found is not None and found.id not in claimed:
                attempt = MatchAttempt(
                    payment_id=found.id,
                    method="provider_id",
                    confidence=_CONFIDENCE_PROVIDER_ID,
                )

        # Strategy 2: amount + date window. Only one (unclaimed) candidate
        # in the window → confident; multiple → fall through to fuzzy.
        candidates: list[Payment] = []
        if attempt is None:
            candidates = await _candidate_payments_in_window(
                db,
                organization_id=tx.organization_id,
                amount=tx.amount,
                transaction_date=tx.transaction_date,
                window_days=window_days,
            )
            candidates = [c for c in candidates if c.id not in claimed]
            if len(candidates) == 1:
                attempt = MatchAttempt(
                    payment_id=candidates[0].id,
                    method="amount_date",
                    confidence=_CONFIDENCE_AMOUNT_DATE,
                )

        # Strategy 3: fuzzy vendor — disambiguate amount+date ties.
        if attempt is None and candidates and tx.counterparty_name:
            fuzzy = await _fuzzy_vendor_match(
                db,
                candidates=candidates,
                counterparty_name=tx.counterparty_name,
            )
            if fuzzy is not None:
                attempt = MatchAttempt(
                    payment_id=fuzzy[0].id,
                    method="fuzzy_vendor",
                    confidence=fuzzy[1],
                )

        if attempt is None:
            counts["unmatched"] += 1
            continue

        tx.matched_payment_id = attempt.payment_id
        tx.match_method = attempt.method
        tx.match_confidence = attempt.confidence
        claimed.add(attempt.payment_id)
        tx.matched_at = datetime.now(UTC)
        counts["matched"] += 1

    return counts
