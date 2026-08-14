"""Bank reconciliation endpoints (``/api/bank-reconciliation``).

Import a bank statement (CSV today; OFX / camt.053 reserved) and auto-match
its debit transactions against our own ``Payment`` rows, so the AP team can
confirm every payment we think we made actually cleared the bank — and catch
one that didn't (or one the bank shows that we have no record of).

All parsing + matching logic is pure and lives in
``app.services.bank_reconciliation`` (``parse_csv_statement`` /
``match_statement_transactions``, shared with no background sweep —
reconciliation is entirely user-triggered, same design as
``vendor_statement_recon``). Money is ``Decimal`` end-to-end; every mutation
is RBAC-gated and writes an audit row. See
``backend/docs/bank-reconciliation.md``.

Not entity-scoped: ``BankStatement`` / ``BankTransaction`` predate the
multi-entity work and cover an org-wide bank account, not a subsidiary's
books — mirrors how ``GLAccount`` treats an unscoped row as shared.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_roles,
)
from app.models.bank_reconciliation import BankStatement, BankTransaction
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.user import User
from app.schemas.bank_reconciliation import (
    AmountMismatchResponse,
    BankStatementListResponse,
    BankStatementResponse,
    BankTransactionResponse,
    OutstandingItemsResponse,
    TransactionResolveRequest,
    UnclearedPaymentResponse,
    UnmatchedDebitResponse,
)
from app.services.audit_dispatch import dispatch_audit
from app.services.bank_reconciliation import (
    MATCH_METHOD_AMOUNT_MISMATCH,
    MATCH_METHOD_MANUAL,
    StatementImportError,
    is_amount_mismatch,
    is_reconciled,
    match_statement_transactions,
    match_variance,
    parse_csv_statement,
)
from app.tenant import get_tenant_db

router = APIRouter(prefix="/bank-reconciliation", tags=["bank-reconciliation"])

_READ_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)
# Bank-statement data is treasury-adjacent (raw account activity) — mutate
# roles mirror Positive Pay's, not the broader AP-clerk read set.
_WRITE_ROLES = (ROLE_ADMIN, ROLE_AP_MANAGER)

_MANUAL_MATCH_CONFIDENCE = Decimal("100.00")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _get_scoped(
    db: AsyncSession, org_id: uuid.UUID, statement_id: uuid.UUID
) -> BankStatement:
    stmt = (
        await db.execute(
            select(BankStatement).where(
                BankStatement.id == statement_id, BankStatement.organization_id == org_id
            )
        )
    ).scalar_one_or_none()
    if stmt is None:
        raise HTTPException(status_code=404, detail="Bank statement not found")
    return stmt


@dataclass(frozen=True)
class _PaymentContext:
    """The matched payment's fields a transaction row needs to render. Fetched
    in one join so a statement's transactions never fan out into an N+1."""

    amount: Decimal
    invoice_number: str | None


async def _matched_payment_context(
    db: AsyncSession, transactions: list[BankTransaction]
) -> dict[uuid.UUID, _PaymentContext]:
    """One query → {payment_id: _PaymentContext} for every matched transaction
    — joins through Payment.invoice_id since a BankTransaction only stores the
    payment FK. The payment's own amount comes back too, so the variance on an
    `amount_mismatch` row is computable without a second request."""
    payment_ids = {t.matched_payment_id for t in transactions if t.matched_payment_id is not None}
    if not payment_ids:
        return {}
    rows = (
        await db.execute(
            select(Payment.id, Payment.amount, Invoice.invoice_number)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(Payment.id.in_(payment_ids))
        )
    ).all()
    return {
        pid: _PaymentContext(amount=amount, invoice_number=number) for pid, amount, number in rows
    }


def _tx_to_response(
    tx: BankTransaction, payment_ctx: dict[uuid.UUID, _PaymentContext]
) -> BankTransactionResponse:
    ctx = payment_ctx.get(tx.matched_payment_id) if tx.matched_payment_id else None
    return BankTransactionResponse(
        id=str(tx.id),
        transaction_date=tx.transaction_date.isoformat(),
        posted_date=tx.posted_date.isoformat() if tx.posted_date else None,
        amount=tx.amount,
        currency=tx.currency,
        description=tx.description,
        counterparty_name=tx.counterparty_name,
        reference=tx.reference,
        direction=tx.direction,
        matched_payment_id=str(tx.matched_payment_id) if tx.matched_payment_id else None,
        matched_invoice_number=ctx.invoice_number if ctx else None,
        match_method=tx.match_method,
        match_confidence=float(tx.match_confidence) if tx.match_confidence is not None else None,
        matched_at=tx.matched_at.isoformat() if tx.matched_at else None,
        matched_payment_amount=ctx.amount if ctx else None,
        variance_amount=match_variance(tx.amount, ctx.amount) if ctx else None,
        is_reconciled=is_reconciled(tx.match_method, tx.matched_payment_id),
    )


def _statement_to_response(
    stmt: BankStatement, *, amount_mismatch_count: int = 0
) -> BankStatementResponse:
    return BankStatementResponse(
        id=str(stmt.id),
        account_identifier=stmt.account_identifier,
        currency=stmt.currency,
        period_start=stmt.period_start.isoformat(),
        period_end=stmt.period_end.isoformat(),
        source_format=stmt.source_format,
        file_key=stmt.file_key,
        opening_balance=stmt.opening_balance,
        closing_balance=stmt.closing_balance,
        transaction_count=stmt.transaction_count,
        matched_count=stmt.matched_count,
        amount_mismatch_count=amount_mismatch_count,
        imported_at=stmt.imported_at.isoformat() if stmt.imported_at else "",
        created_at=stmt.created_at.isoformat() if stmt.created_at else "",
        transactions=None,
    )


async def _amount_mismatch_counts(
    db: AsyncSession, statement_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """One grouped query → {statement_id: amount_mismatch count} for a whole
    page of statements. A discrepancy a user has to open each statement to
    notice is a discrepancy nobody notices, so the list carries it too."""
    if not statement_ids:
        return {}
    rows = (
        await db.execute(
            select(BankTransaction.statement_id, func.count())
            .where(
                BankTransaction.statement_id.in_(statement_ids),
                BankTransaction.match_method == MATCH_METHOD_AMOUNT_MISMATCH,
            )
            .group_by(BankTransaction.statement_id)
        )
    ).all()
    return {sid: count for sid, count in rows}


async def _recompute_matched_count(db: AsyncSession, stmt: BankStatement) -> None:
    """Refresh the denormalised rollup from the full transaction set, counting
    RECONCILED lines only (`is_reconciled`) — an `amount_mismatch` is linked to
    a payment but has not cleared."""
    all_tx = list(
        (await db.execute(select(BankTransaction).where(BankTransaction.statement_id == stmt.id)))
        .scalars()
        .all()
    )
    stmt.matched_count = sum(
        1 for t in all_tx if is_reconciled(t.match_method, t.matched_payment_id)
    )


async def _detail_response(db: AsyncSession, stmt: BankStatement) -> BankStatementResponse:
    transactions = list(
        (
            await db.execute(
                select(BankTransaction)
                .where(BankTransaction.statement_id == stmt.id)
                .order_by(BankTransaction.transaction_date)
            )
        )
        .scalars()
        .all()
    )
    payment_ctx = await _matched_payment_context(db, transactions)
    rows = [_tx_to_response(t, payment_ctx) for t in transactions]
    resp = _statement_to_response(
        stmt,
        amount_mismatch_count=sum(
            1 for r in rows if r.match_method == MATCH_METHOD_AMOUNT_MISMATCH
        ),
    )
    resp.transactions = rows
    return resp


# --------------------------------------------------------------------------- #
# Import
# --------------------------------------------------------------------------- #


@router.post("/upload", response_model=BankStatementResponse, status_code=status.HTTP_201_CREATED)
async def upload_statement(
    file: UploadFile = File(...),
    account_identifier: str = Form(...),
    period_start: date = Form(...),
    period_end: date = Form(...),
    currency: str = Form("USD"),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    raw = await file.read()
    try:
        statement, transactions = parse_csv_statement(
            raw_csv=raw,
            organization_id=org_id,
            account_identifier=account_identifier,
            period_start=period_start,
            period_end=period_end,
            currency=currency,
            imported_by=user.id,
            # Raw-file storage (the uploaded statement → S3) is deferred,
            # same as vendor-statement-recon's CSV intake; keep file_key
            # NULL for now. See docs § Deferred.
            file_key=None,
        )
    except StatementImportError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    db.add(statement)
    await db.flush()  # assign statement.id before stamping the children

    for tx in transactions:
        tx.statement_id = statement.id
    db.add_all(transactions)

    counts = await match_statement_transactions(db, transactions)
    statement.matched_count = counts["matched"]

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="bank_reconciliation.imported",
        entity_type="bank_statement",
        entity_id=statement.id,
        details={
            "account_identifier": account_identifier,
            "transaction_count": statement.transaction_count,
            **counts,
        },
    )
    await db.commit()
    await db.refresh(statement)
    return await _detail_response(db, statement)


# --------------------------------------------------------------------------- #
# List / detail
# --------------------------------------------------------------------------- #


@router.get("", response_model=BankStatementListResponse)
async def list_statements(
    account_identifier: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    query = select(BankStatement).where(BankStatement.organization_id == org_id)
    if account_identifier:
        query = query.where(BankStatement.account_identifier == account_identifier)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = (
        query.order_by(BankStatement.imported_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list((await db.execute(query)).scalars().all())
    mismatch_counts = await _amount_mismatch_counts(db, [r.id for r in rows])
    return BankStatementListResponse(
        items=[
            _statement_to_response(r, amount_mismatch_count=mismatch_counts.get(r.id, 0))
            for r in rows
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


# --------------------------------------------------------------------------- #
# Outstanding items — the org-wide close view
# --------------------------------------------------------------------------- #
#
# Declared BEFORE `/{statement_id}`: FastAPI matches routes in declaration
# order and `statement_id: uuid.UUID` would 422 on the literal "outstanding"
# rather than falling through.


# Statuses where our books assert the money has been handed to the bank, so a
# corresponding debit is expected. `pending` has not been dispatched;
# `failed`/`cancelled`/`voided` are terminal non-payments; `pending_compliance`
# is held BEFORE the adapter call. None of those should read as outstanding.
_EXPECTED_TO_CLEAR_STATUSES = ("completed", "submitted", "processing")


@router.get("/outstanding", response_model=OutstandingItemsResponse)
async def outstanding_items(
    older_than_days: int = Query(
        0,
        ge=0,
        le=3650,
        description="Only report payments sent at least this many days ago.",
    ),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """The three buckets a bank-reconciliation worksheet closes a period on.

    Per-statement detail answers "did this file reconcile"; nothing answered
    "across everything we have imported, what has still not cleared" — the
    question month-end actually asks. Computed on read from the existing
    `BankTransaction.matched_payment_id` link, so there is no stored clearance
    column to drift out of sync when a payment is later voided or a match
    re-pointed.

      * ``uncleared_payments``  — we say it went out, no bank line claims it.
      * ``unmatched_debits``    — money left the account with no payment behind
                                  it (the never-issued-cheque shape).
      * ``amount_mismatches``   — identified, but the bank moved a different
                                  amount. Positive variance = they took more.

    A payment linked to an ``amount_mismatch`` line is NOT uncleared — it is
    accounted for, in the mismatch bucket — so it appears exactly once.

    Each bucket runs a SQL aggregate for its count + exact ``Decimal`` total
    and a separate ``LIMIT``-ed row query, so ``?limit`` truncates the rows
    only and nothing unbounded is ever loaded into memory — a month-end close
    on a large backlog is the exact shape this endpoint has to survive.
    """
    today = datetime.now(UTC).date()
    cutoff = today - timedelta(days=older_than_days)

    claimed_subq = select(BankTransaction.matched_payment_id).where(
        BankTransaction.matched_payment_id.is_not(None)
    )

    # ---- Bucket 1: payments no bank line claims ---------------------------
    # Same fallback chain the matcher's date window uses, so "outstanding
    # since" and "matchable around" agree on when we consider a payment sent.
    # Expressed in SQL rather than Python so the age filter, the aggregate and
    # the LIMIT all run in the database — an org with a large unreconciled
    # backlog must not pull every qualifying row into memory just to total it.
    sent_at_expr = func.coalesce(Payment.submitted_at, Payment.completed_at, Payment.created_at)
    # Normalise to the UTC calendar date explicitly rather than casting a
    # `timestamptz` straight to `date`, which resolves against the Postgres
    # session `timezone` GUC. `cutoff` and `today` are both derived from
    # `datetime.now(UTC)`, so a server session on a non-UTC zone would compare
    # a local-calendar date against a UTC one and shift the boundary — and
    # `days_outstanding` with it — by a day.
    sent_on_expr = cast(func.timezone("UTC", sent_at_expr), Date)
    uncleared_where = (
        Payment.status.in_(_EXPECTED_TO_CLEAR_STATUSES),
        Payment.id.not_in(claimed_subq),
        # A row with no usable timestamp at all can't be aged out — surface it
        # rather than hide it behind a filter it can never satisfy.
        or_(sent_at_expr.is_(None), sent_on_expr <= cutoff),
    )

    # Counts + totals cover EVERY outstanding payment; only the row list below
    # is capped at `limit`, so a truncated page never understates the money.
    # The aggregate joins Invoice exactly like the row query does even though it
    # reads no column from it: `Payment.invoice_id` is a NOT NULL FK so the join
    # cannot drop a row today, but two query shapes that disagree about which
    # rows qualify are how a count silently stops matching its own list.
    uncleared_count, uncleared_sum = (
        await db.execute(
            select(func.count(), func.sum(Payment.amount))
            .select_from(Payment)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(*uncleared_where)
        )
    ).one()
    uncleared_total = uncleared_sum if uncleared_sum is not None else Decimal("0.00")

    uncleared_rows = (
        await db.execute(
            select(Payment, Invoice.invoice_number, Invoice.vendor_name, sent_on_expr)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(*uncleared_where)
            .order_by(Payment.created_at)
            .limit(limit)
        )
    ).all()

    uncleared = [
        UnclearedPaymentResponse(
            payment_id=str(payment.id),
            invoice_id=str(payment.invoice_id),
            invoice_number=invoice_number,
            vendor_name=vendor_name,
            amount=payment.amount,
            method=payment.method,
            status=payment.status,
            sent_on=sent_on.isoformat() if sent_on else None,
            days_outstanding=(today - sent_on).days if sent_on else None,
        )
        for payment, invoice_number, vendor_name, sent_on in uncleared_rows
    ]

    # ---- Bucket 2: bank debits with no payment behind them ----------------
    unmatched_where = (
        BankStatement.organization_id == org_id,
        BankTransaction.direction == "debit",
        BankTransaction.matched_payment_id.is_(None),
    )
    unmatched_count, unmatched_sum = (
        await db.execute(
            select(func.count(), func.sum(BankTransaction.amount))
            .select_from(BankTransaction)
            .join(BankStatement, BankStatement.id == BankTransaction.statement_id)
            .where(*unmatched_where)
        )
    ).one()
    unmatched_total = unmatched_sum if unmatched_sum is not None else Decimal("0.00")

    unmatched_rows = (
        await db.execute(
            select(BankTransaction, BankStatement.account_identifier)
            .join(BankStatement, BankStatement.id == BankTransaction.statement_id)
            .where(*unmatched_where)
            .order_by(BankTransaction.transaction_date.desc())
            .limit(limit)
        )
    ).all()

    unmatched = [
        UnmatchedDebitResponse(
            transaction_id=str(tx.id),
            statement_id=str(tx.statement_id),
            account_identifier=account_identifier,
            transaction_date=tx.transaction_date.isoformat(),
            amount=tx.amount,
            currency=tx.currency,
            counterparty_name=tx.counterparty_name,
            reference=tx.reference,
            description=tx.description,
        )
        for tx, account_identifier in unmatched_rows
    ]

    # ---- Bucket 3: identified, but the bank moved a different amount ------
    # Inner-joined to Payment, so the variance is computed by the database from
    # both sides — never from a float, and never from a stale copy.
    mismatch_where = (
        BankStatement.organization_id == org_id,
        BankTransaction.direction == "debit",
        BankTransaction.match_method == MATCH_METHOD_AMOUNT_MISMATCH,
    )
    # Same join set as the row query below (Invoice included, though the
    # aggregate reads nothing from it) so the count can never disagree with the
    # list it heads.
    mismatch_count, variance_sum = (
        await db.execute(
            select(func.count(), func.sum(BankTransaction.amount - Payment.amount))
            .select_from(BankTransaction)
            .join(BankStatement, BankStatement.id == BankTransaction.statement_id)
            .join(Payment, Payment.id == BankTransaction.matched_payment_id)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(*mismatch_where)
        )
    ).one()
    net_variance = variance_sum if variance_sum is not None else Decimal("0.00")

    mismatch_rows = (
        await db.execute(
            select(
                BankTransaction,
                BankStatement.account_identifier,
                Payment.amount,
                Invoice.invoice_number,
            )
            .join(BankStatement, BankStatement.id == BankTransaction.statement_id)
            .join(Payment, Payment.id == BankTransaction.matched_payment_id)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(*mismatch_where)
            .order_by(BankTransaction.transaction_date.desc())
            .limit(limit)
        )
    ).all()

    mismatches = [
        AmountMismatchResponse(
            transaction_id=str(tx.id),
            statement_id=str(tx.statement_id),
            account_identifier=account_identifier,
            transaction_date=tx.transaction_date.isoformat(),
            bank_amount=tx.amount,
            payment_amount=payment_amount,
            variance_amount=match_variance(tx.amount, payment_amount),
            payment_id=str(tx.matched_payment_id),
            invoice_number=invoice_number,
            counterparty_name=tx.counterparty_name,
        )
        for tx, account_identifier, payment_amount, invoice_number in mismatch_rows
    ]

    return OutstandingItemsResponse(
        as_of=today.isoformat(),
        older_than_days=older_than_days,
        uncleared_payments=uncleared,
        uncleared_count=uncleared_count,
        uncleared_total=uncleared_total,
        unmatched_debits=unmatched,
        unmatched_debit_count=unmatched_count,
        unmatched_debit_total=unmatched_total,
        amount_mismatches=mismatches,
        amount_mismatch_count=mismatch_count,
        amount_mismatch_net_variance=net_variance,
    )


@router.get("/{statement_id}", response_model=BankStatementResponse)
async def get_statement(
    statement_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_READ_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    stmt = await _get_scoped(db, org_id, statement_id)
    return await _detail_response(db, stmt)


# --------------------------------------------------------------------------- #
# Manual match review
# --------------------------------------------------------------------------- #


@router.post(
    "/{statement_id}/transactions/{transaction_id}/resolve", response_model=BankStatementResponse
)
async def resolve_transaction(
    statement_id: uuid.UUID,
    transaction_id: uuid.UUID,
    body: TransactionResolveRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    stmt = await _get_scoped(db, org_id, statement_id)
    tx = (
        await db.execute(
            select(BankTransaction).where(
                BankTransaction.id == transaction_id, BankTransaction.statement_id == stmt.id
            )
        )
    ).scalar_one_or_none()
    if tx is None:
        raise HTTPException(status_code=404, detail="Bank transaction not found")

    variance_str: str | None = None
    if body.matched_payment_id is not None:
        payment_id = uuid.UUID(body.matched_payment_id)
        # Row-lock the payment being claimed. The "already matched elsewhere"
        # check below is a read-then-write, so two concurrent resolves pointing
        # DIFFERENT transactions at the SAME payment both used to read "not
        # claimed", both pass, and both commit — the payment ends up counted as
        # cleared twice, which is precisely the double-count the check exists to
        # prevent. Every claimant must take this lock first, so the second
        # blocks until the first commits and then sees the claim. Mirrors the
        # money-path convention in `api/payments.py` (`/approve`, `/execute`,
        # `/cancel`, `/void` all lock the row they gate on).
        payment = (
            await db.execute(select(Payment).where(Payment.id == payment_id).with_for_update())
        ).scalar_one_or_none()
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        # A Payment can be matched to at most one BankTransaction — same
        # invariant the automatic matcher enforces (see
        # services.bank_reconciliation.match_statement_transactions).
        # Without this check a clerk could manually point two different
        # transactions at the same payment, double-counting it as cleared.
        #
        # `.first()` on a LIMIT 1, not `scalar_one_or_none()`: there is no
        # unique index behind this invariant, so pre-existing data can already
        # hold more than one claimant, and asking for exactly-one would 500 on
        # the very rows this check exists to reject.
        other = (
            (
                await db.execute(
                    select(BankTransaction.id)
                    .where(
                        BankTransaction.matched_payment_id == payment_id,
                        BankTransaction.id != tx.id,
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if other is not None:
            raise HTTPException(
                status_code=409,
                detail="This payment is already matched to another bank transaction.",
            )
        tx.matched_payment_id = payment.id
        # The classification is DERIVED from the two amounts, never asserted by
        # the caller: a human pointing a $10 bank line at a $10,000 payment is
        # telling us which payment it is, not that the amounts agree. Stamping
        # it "manual, confidence 100" would let a clerk click straight past the
        # altered-amount signal the auto-matcher raises.
        tx.match_method = (
            MATCH_METHOD_AMOUNT_MISMATCH
            if is_amount_mismatch(tx.amount, payment.amount)
            else MATCH_METHOD_MANUAL
        )
        tx.match_confidence = _MANUAL_MATCH_CONFIDENCE
        tx.matched_at = datetime.now(UTC)
        variance_str = str(match_variance(tx.amount, payment.amount))
    else:
        tx.matched_payment_id = None
        tx.match_method = None
        tx.match_confidence = None
        tx.matched_at = None

    await _recompute_matched_count(db, stmt)

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="bank_reconciliation.transaction_resolved",
        entity_type="bank_statement",
        entity_id=stmt.id,
        details={
            "transaction_id": str(tx.id),
            "matched_payment_id": body.matched_payment_id,
            # Exact string, never float — the audit row is the durable record
            # of a discrepancy a human accepted responsibility for.
            "match_method": tx.match_method,
            "variance_amount": variance_str,
        },
    )
    await db.commit()
    await db.refresh(stmt)
    return await _detail_response(db, stmt)


@router.delete("/{statement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_statement(
    statement_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(*_WRITE_ROLES)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    stmt = await _get_scoped(db, org_id, statement_id)
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="bank_reconciliation.deleted",
        entity_type="bank_statement",
        entity_id=stmt.id,
        details={"account_identifier": stmt.account_identifier},
    )
    await db.delete(stmt)  # cascade removes the transactions
    await db.commit()
