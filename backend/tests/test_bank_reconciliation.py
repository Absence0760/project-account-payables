"""Bank statement parsing + payment-matching tests.

Two units under test:

  1. `parse_csv_statement` — turn a CSV bytes blob into
     `BankStatement` + `BankTransaction` rows. Real bank exports
     come in dozens of column-naming conventions; the parser sniffs
     the header row and accepts common synonyms. Tests pin:
       - common column-name variants (Date / Transaction Date /
         Posted Date; Amount / Value / Withdrawal+Deposit pair)
       - signed-amount columns AND separate debit/credit columns
       - `(1,234.56)` and `-1,234.56` both parse as negatives
       - ISO + US-style date formats
       - empty body / no-header / no-rows all raise
         `StatementImportError`
       - one bad row in a sea of good rows is skipped silently,
         the rest still parse

  2. `match_statement_transactions` — strategies:
       - exact match on `provider_payment_id` → confidence 100,
         method=provider_id
       - amount + date with exactly one candidate → confidence 80,
         method=amount_date
       - amount + date with multiple candidates + fuzzy vendor →
         confidence 50–70, method=fuzzy_vendor
       - amount + date with multiple candidates + no fuzzy hit →
         unmatched (we don't pick arbitrarily)
       - credit transactions skipped entirely
       - the running counts returned by the matcher reflect the
         outcome bucketing

A regression that mis-counted matched / unmatched is a soft fail
(operations annoyance). A regression that matched the wrong payment
is a hard fail — it credits the wrong invoice and breaks GL.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.bank_reconciliation import (
    StatementImportError,
    match_statement_transactions,
    parse_csv_statement,
)


def _csv(*lines: str) -> bytes:
    return ("\n".join(lines) + "\n").encode()


# ---------------------------------------------------------------------------
# parse_csv_statement — happy paths.
# ---------------------------------------------------------------------------


def test_parse_simple_iso_dates_and_signed_amount_column():
    """Most common shape: Date, Description, Amount. Signed amount
    where negatives are debits."""
    raw = _csv(
        "Date,Description,Amount",
        "2026-05-01,Acme Corp,-1234.56",
        "2026-05-02,Refund Co,250.00",
    )
    stmt, txs = parse_csv_statement(
        raw_csv=raw,
        organization_id=uuid.uuid4(),
        account_identifier="****1234",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )
    assert stmt.transaction_count == 2
    assert txs[0].direction == "debit"
    assert txs[0].amount == Decimal("1234.56")
    assert txs[1].direction == "credit"
    assert txs[1].amount == Decimal("250.00")


def test_parse_separate_debit_and_credit_columns():
    """US bank style: Date, Description, Debit, Credit (one or
    the other is filled per row)."""
    raw = _csv(
        "Date,Description,Debit,Credit",
        "05/01/2026,Acme Corp,100.00,",
        "05/02/2026,Salary,,5000.00",
    )
    _, txs = parse_csv_statement(
        raw_csv=raw,
        organization_id=uuid.uuid4(),
        account_identifier="****1234",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )
    assert len(txs) == 2
    assert txs[0].direction == "debit"
    assert txs[0].amount == Decimal("100.00")
    assert txs[1].direction == "credit"
    assert txs[1].amount == Decimal("5000.00")


def test_parse_handles_parenthesized_negative_amounts():
    """Quickbooks-style export: `(1,234.56)` means negative."""
    raw = _csv(
        "Date,Memo,Amount",
        '2026-05-01,Acme Corp,"(1,234.56)"',
    )
    _, txs = parse_csv_statement(
        raw_csv=raw,
        organization_id=uuid.uuid4(),
        account_identifier="x",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )
    assert txs[0].direction == "debit"
    assert txs[0].amount == Decimal("1234.56")


def test_parse_handles_comma_thousands_and_dollar_signs():
    raw = _csv(
        "Date,Description,Amount",
        '2026-05-01,X,"$1,234.56"',
    )
    _, txs = parse_csv_statement(
        raw_csv=raw,
        organization_id=uuid.uuid4(),
        account_identifier="x",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )
    assert txs[0].amount == Decimal("1234.56")


def test_parse_carries_reference_and_counterparty_columns():
    """Reference + counterparty columns are surfaced for the matcher
    to use. The raw row is also stashed in `raw_data` for audit
    replay."""
    raw = _csv(
        "Date,Counterparty,Amount,Reference",
        "2026-05-01,Acme Corp,-100.00,REF-9999",
    )
    _, txs = parse_csv_statement(
        raw_csv=raw,
        organization_id=uuid.uuid4(),
        account_identifier="x",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )
    assert txs[0].counterparty_name == "Acme Corp"
    assert txs[0].reference == "REF-9999"
    # Raw row preserved.
    assert txs[0].raw_data == {
        "Date": "2026-05-01",
        "Counterparty": "Acme Corp",
        "Amount": "-100.00",
        "Reference": "REF-9999",
    }


def test_parse_skips_bad_rows_silently_keeps_good_ones():
    """One bad row mid-file should not poison the whole import.
    The matcher works on whatever rows did parse."""
    raw = _csv(
        "Date,Description,Amount",
        "2026-05-01,Acme,-100.00",
        "not-a-date,Bad,xyz",
        "2026-05-02,Globex,-200.00",
    )
    _, txs = parse_csv_statement(
        raw_csv=raw,
        organization_id=uuid.uuid4(),
        account_identifier="x",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )
    assert len(txs) == 2  # the bad row was skipped


# ---------------------------------------------------------------------------
# parse_csv_statement — refusal paths.
# ---------------------------------------------------------------------------


def test_parse_empty_csv_raises():
    with pytest.raises(StatementImportError, match="empty"):
        parse_csv_statement(
            raw_csv=b"",
            organization_id=uuid.uuid4(),
            account_identifier="x",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )


def test_parse_header_only_raises():
    """Header but no data rows is the same shape as a misclick on
    "export" — surface the error so the operator notices instead of
    creating a zero-transaction statement."""
    with pytest.raises(StatementImportError):
        parse_csv_statement(
            raw_csv=b"Date,Amount\n",
            organization_id=uuid.uuid4(),
            account_identifier="x",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )


def test_parse_header_without_date_column_raises():
    raw = _csv("Description,Amount", "Acme,-100.00")
    with pytest.raises(StatementImportError, match="date column"):
        parse_csv_statement(
            raw_csv=raw,
            organization_id=uuid.uuid4(),
            account_identifier="x",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )


def test_parse_header_without_amount_or_debit_credit_raises():
    raw = _csv("Date,Description", "2026-05-01,Acme")
    with pytest.raises(StatementImportError, match="amount"):
        parse_csv_statement(
            raw_csv=raw,
            organization_id=uuid.uuid4(),
            account_identifier="x",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )


def test_parse_all_rows_unparseable_raises():
    """Header is fine but every row is structurally bad → raise (not
    silently produce a zero-tx statement)."""
    raw = _csv(
        "Date,Amount",
        "bad-date,bad-amount",
        "another-bad,oops",
    )
    with pytest.raises(StatementImportError, match="no parseable"):
        parse_csv_statement(
            raw_csv=raw,
            organization_id=uuid.uuid4(),
            account_identifier="x",
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
        )


def test_parse_falls_back_to_latin1_for_non_utf8_input():
    """Some bank exports use cp1252. Don't crash — fall back."""
    raw = "Date,Description,Amount\n2026-05-01,Naïve Café,-100.00\n".encode("latin-1")
    _, txs = parse_csv_statement(
        raw_csv=raw,
        organization_id=uuid.uuid4(),
        account_identifier="x",
        period_start=date(2026, 5, 1),
        period_end=date(2026, 5, 31),
    )
    assert len(txs) == 1


# ---------------------------------------------------------------------------
# match_statement_transactions — strategy 1: provider_id exact.
# ---------------------------------------------------------------------------


def _tx(
    *,
    amount=Decimal("100"),
    date_=None,
    reference=None,
    counterparty=None,
    direction="debit",
    organization_id=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id or uuid.uuid4(),
        transaction_date=date_ or date(2026, 5, 1),
        amount=amount,
        direction=direction,
        reference=reference,
        counterparty_name=counterparty,
        description=None,
        matched_payment_id=None,
        match_method=None,
        match_confidence=None,
        matched_at=None,
    )


def _payment(
    *,
    amount=Decimal("100"),
    submitted_at=None,
    provider_payment_id=None,
    reference=None,
    invoice_id=None,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        invoice_id=invoice_id or uuid.uuid4(),
        amount=amount,
        submitted_at=submitted_at,
        completed_at=None,
        created_at=submitted_at,
        provider_payment_id=provider_payment_id,
        reference=reference,
    )


def _invoice(*, vendor_name="Acme Corp", id_=None):
    return SimpleNamespace(id=id_ or uuid.uuid4(), vendor_name=vendor_name)


def _mock_db(*, by_provider_id=None, by_reference=None, payments=None, invoices=None):
    """Build a DB whose execute() returns the right shape based on
    SQL pattern matching the production queries. We sniff the
    rendered SQL because match_statement_transactions issues a
    handful of distinct queries."""
    db = AsyncMock()
    by_provider_id = by_provider_id or {}
    by_reference = by_reference or {}
    payments = payments or []
    invoices = invoices or []

    async def _execute(query):
        sql = str(query).lower()
        result = MagicMock()
        # Anchor on the WHERE-clause shape, not on column names in
        # the SELECT list — every Payment query has provider_payment_id
        # / reference as selected columns.
        if "where payments.provider_payment_id = :" in sql:
            params = query.compile().params
            pid = params.get("provider_payment_id_1")
            p = by_provider_id.get(pid)
            result.scalar_one_or_none = MagicMock(return_value=p)
            return result
        if "where payments.reference = :" in sql:
            params = query.compile().params
            ref = params.get("reference_1")
            p = by_reference.get(ref)
            result.scalar_one_or_none = MagicMock(return_value=p)
            return result
        if "from invoices" in sql:
            scalars = MagicMock()
            scalars.all = MagicMock(return_value=invoices)
            result.scalars = MagicMock(return_value=scalars)
            return result
        # Default: payments fan-out (the amount+date window pull —
        # WHERE on Payment.amount only). Filter by the bound amount
        # so an unrelated transaction in the same batch doesn't
        # incorrectly match.
        params = query.compile().params
        target_amount = params.get("amount_1")
        filtered = [p for p in payments if target_amount is None or p.amount == target_amount]
        scalars = MagicMock()
        scalars.all = MagicMock(return_value=filtered)
        result.scalars = MagicMock(return_value=scalars)
        return result

    db.execute = AsyncMock(side_effect=_execute)
    return db


@pytest.mark.asyncio
async def test_match_by_provider_payment_id_wins_with_full_confidence():
    """When the bank gives us back the processor's payment ID in the
    `reference` column, we match exactly. Confidence 100, method
    provider_id, no need to consult the date window."""
    payment = _payment(provider_payment_id="prov_xyz_123")
    tx = _tx(reference="prov_xyz_123", amount=Decimal("100"))
    db = _mock_db(by_provider_id={"prov_xyz_123": payment})

    counts = await match_statement_transactions(db, [tx])
    assert counts == {"matched": 1, "unmatched": 0, "skipped_credit": 0}
    assert tx.matched_payment_id == payment.id
    assert tx.match_method == "provider_id"
    assert tx.match_confidence == Decimal("100.00")
    assert tx.matched_at is not None


# ---------------------------------------------------------------------------
# Strategy 2: amount + date single candidate.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_by_amount_and_date_single_candidate():
    """No reference hit, but exactly one Payment matches amount +
    submitted_at within the 5-day window → confidence 80, method
    amount_date."""
    payment = _payment(
        amount=Decimal("100"),
        submitted_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    tx = _tx(amount=Decimal("100"), date_=date(2026, 5, 3))
    db = _mock_db(payments=[payment])

    counts = await match_statement_transactions(db, [tx])
    assert counts["matched"] == 1
    assert tx.match_method == "amount_date"
    assert tx.match_confidence == Decimal("80.00")


@pytest.mark.asyncio
async def test_match_skips_credit_transactions():
    """Credits aren't payments we made — skip without scoring."""
    tx = _tx(direction="credit", amount=Decimal("100"))
    db = _mock_db()

    counts = await match_statement_transactions(db, [tx])
    assert counts == {"matched": 0, "unmatched": 0, "skipped_credit": 1}
    assert tx.matched_payment_id is None


@pytest.mark.asyncio
async def test_match_no_candidates_in_window_leaves_unmatched():
    """The transaction's date is way outside the 5-day window from
    every candidate → unmatched."""
    payment = _payment(
        amount=Decimal("100"),
        submitted_at=datetime(2026, 1, 1, tzinfo=UTC),  # 4 months earlier
    )
    tx = _tx(amount=Decimal("100"), date_=date(2026, 5, 15))
    db = _mock_db(payments=[payment])

    counts = await match_statement_transactions(db, [tx])
    assert counts["unmatched"] == 1
    assert tx.matched_payment_id is None


# ---------------------------------------------------------------------------
# Strategy 3: fuzzy vendor disambiguates.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_match_amount_date_ambiguous_resolved_by_fuzzy_vendor():
    """Two payments same amount in the window → fuzzy vendor name
    decides. Confidence 50–70 reflects the name overlap."""
    inv_acme = _invoice(vendor_name="Acme Industries Inc")
    inv_globex = _invoice(vendor_name="Globex Corp")
    p_acme = _payment(
        amount=Decimal("100"),
        submitted_at=datetime(2026, 5, 1, tzinfo=UTC),
        invoice_id=inv_acme.id,
    )
    p_globex = _payment(
        amount=Decimal("100"),
        submitted_at=datetime(2026, 5, 1, tzinfo=UTC),
        invoice_id=inv_globex.id,
    )
    tx = _tx(
        amount=Decimal("100"),
        date_=date(2026, 5, 2),
        counterparty="Acme Industries",
    )
    db = _mock_db(payments=[p_acme, p_globex], invoices=[inv_acme, inv_globex])

    counts = await match_statement_transactions(db, [tx])
    assert counts["matched"] == 1
    assert tx.matched_payment_id == p_acme.id
    assert tx.match_method == "fuzzy_vendor"
    # Fuzzy confidence falls in the 50–70 band.
    assert Decimal("50") <= tx.match_confidence <= Decimal("70")


@pytest.mark.asyncio
async def test_match_amount_date_ambiguous_with_no_fuzzy_hit_leaves_unmatched():
    """Two candidates, but the bank's counterparty name doesn't
    match either invoice's vendor — refuse to pick arbitrarily.
    Better to leave unmatched than to credit the wrong invoice."""
    inv_a = _invoice(vendor_name="Globex Corp")
    inv_b = _invoice(vendor_name="Pied Piper")
    p_a = _payment(
        amount=Decimal("100"),
        submitted_at=datetime(2026, 5, 1, tzinfo=UTC),
        invoice_id=inv_a.id,
    )
    p_b = _payment(
        amount=Decimal("100"),
        submitted_at=datetime(2026, 5, 1, tzinfo=UTC),
        invoice_id=inv_b.id,
    )
    tx = _tx(
        amount=Decimal("100"),
        date_=date(2026, 5, 2),
        counterparty="Acme Industries",  # disjoint from both
    )
    db = _mock_db(payments=[p_a, p_b], invoices=[inv_a, inv_b])

    counts = await match_statement_transactions(db, [tx])
    assert counts["unmatched"] == 1
    assert tx.matched_payment_id is None


@pytest.mark.asyncio
async def test_match_returns_full_outcome_counts():
    """Mixed batch: one provider-id hit, one amount-date hit, one
    credit, one unmatched. Counts must reflect each."""
    p1 = _payment(provider_payment_id="prov_1")
    p2 = _payment(
        amount=Decimal("250"),
        submitted_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    txs = [
        _tx(reference="prov_1", amount=Decimal("100")),  # provider_id
        _tx(amount=Decimal("250"), date_=date(2026, 5, 2)),  # amount_date
        _tx(direction="credit", amount=Decimal("500")),  # skipped
        _tx(amount=Decimal("999")),  # unmatched
    ]
    db = _mock_db(
        by_provider_id={"prov_1": p1},
        payments=[p2],
    )

    counts = await match_statement_transactions(db, txs)
    assert counts == {"matched": 2, "unmatched": 1, "skipped_credit": 1}
