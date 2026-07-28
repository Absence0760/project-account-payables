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
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
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
    BankStatementListResponse,
    BankStatementResponse,
    BankTransactionResponse,
    TransactionResolveRequest,
)
from app.services.audit_dispatch import dispatch_audit
from app.services.bank_reconciliation import (
    StatementImportError,
    match_statement_transactions,
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


async def _matched_invoice_numbers(
    db: AsyncSession, transactions: list[BankTransaction]
) -> dict[uuid.UUID, str]:
    """One query → {payment_id: invoice_number} for every matched transaction
    (no N+1) — joins through Payment.invoice_id since a BankTransaction only
    stores the payment FK."""
    payment_ids = {t.matched_payment_id for t in transactions if t.matched_payment_id is not None}
    if not payment_ids:
        return {}
    rows = (
        await db.execute(
            select(Payment.id, Invoice.invoice_number)
            .join(Invoice, Invoice.id == Payment.invoice_id)
            .where(Payment.id.in_(payment_ids))
        )
    ).all()
    return {pid: number for pid, number in rows}


def _tx_to_response(
    tx: BankTransaction, matched_numbers: dict[uuid.UUID, str]
) -> BankTransactionResponse:
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
        matched_invoice_number=(
            matched_numbers.get(tx.matched_payment_id) if tx.matched_payment_id else None
        ),
        match_method=tx.match_method,
        match_confidence=float(tx.match_confidence) if tx.match_confidence is not None else None,
        matched_at=tx.matched_at.isoformat() if tx.matched_at else None,
    )


def _statement_to_response(
    stmt: BankStatement, *, transactions: list[BankTransaction] | None = None
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
        imported_at=stmt.imported_at.isoformat() if stmt.imported_at else "",
        created_at=stmt.created_at.isoformat() if stmt.created_at else "",
        transactions=None,
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
    matched_numbers = await _matched_invoice_numbers(db, transactions)
    resp = _statement_to_response(stmt)
    resp.transactions = [_tx_to_response(t, matched_numbers) for t in transactions]
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
    return BankStatementListResponse(
        items=[_statement_to_response(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
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

    if body.matched_payment_id is not None:
        payment_id = uuid.UUID(body.matched_payment_id)
        payment = (
            await db.execute(select(Payment).where(Payment.id == payment_id))
        ).scalar_one_or_none()
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        # A Payment can be matched to at most one BankTransaction — same
        # invariant the automatic matcher enforces (see
        # services.bank_reconciliation.match_statement_transactions).
        # Without this check a clerk could manually point two different
        # transactions at the same payment, double-counting it as cleared.
        other = (
            await db.execute(
                select(BankTransaction.id).where(
                    BankTransaction.matched_payment_id == payment_id,
                    BankTransaction.id != tx.id,
                )
            )
        ).scalar_one_or_none()
        if other is not None:
            raise HTTPException(
                status_code=409,
                detail="This payment is already matched to another bank transaction.",
            )
        tx.matched_payment_id = payment.id
        tx.match_method = "manual"
        tx.match_confidence = _MANUAL_MATCH_CONFIDENCE
        tx.matched_at = datetime.now(UTC)
    else:
        tx.matched_payment_id = None
        tx.match_method = None
        tx.match_confidence = None
        tx.matched_at = None

    # Recompute the denormalised rollup from the full transaction set.
    all_tx = list(
        (await db.execute(select(BankTransaction).where(BankTransaction.statement_id == stmt.id)))
        .scalars()
        .all()
    )
    stmt.matched_count = sum(1 for t in all_tx if t.matched_payment_id is not None)

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
