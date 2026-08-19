"""Contract compliance monitoring — flag invoice spend that falls outside the
terms of its linked contract.

Pure-ish evaluator: given an invoice with a ``contract_id``, it loads the
contract and returns a list of finding dicts (``{type, severity, message}``).
Exception creation + persistence is the caller's job
(``invoice_warnings.refresh_warnings``), so this stays unit-testable in
isolation. Money math is exact — the cumulative-spend sum runs over the
``Numeric`` ``amount`` column and is compared as ``Decimal``.

Checks (all advisory unless the contract is ``not_to_exceed``):
  * spend recorded after the contract's ``end_date`` (expired) or before its
    ``start_date``
  * cumulative linked spend over the contract's ``spend_limit`` — ``error``
    severity when ``not_to_exceed``, else ``warning``. The cumulative sum is
    scoped to the contract's own ``currency`` (never add unlike face values —
    same rule as ``contract_spend`` / ``budget_service``); an invoice in a
    different currency than the contract cannot itself be summed against the
    limit, so it skips this specific check rather than comparing unlike money.
  * invoice vendor differs from the contract vendor
  * invoice GL account outside the contract's ``terms.allowed_gl_accounts``
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract, ContractStatus
from app.models.invoice import Invoice, InvoiceStatus
from app.utils.dates import utc_today

COMPLIANCE_EXCEPTION_TYPE = "contract_noncompliant"


async def evaluate_contract_compliance(
    db: AsyncSession,
    invoice: Invoice,
    *,
    today: date | None = None,
) -> list[dict]:
    """Return contract-compliance findings for ``invoice``.

    Empty list when the invoice isn't linked to a contract, or the link
    dangles (contract deleted) — a dangling link is not a compliance finding.
    """
    if not invoice.contract_id:
        return []

    contract = (
        await db.execute(select(Contract).where(Contract.id == invoice.contract_id))
    ).scalar_one_or_none()
    if contract is None:
        return []

    findings: list[dict] = []
    ref_date = invoice.invoice_date or today or utc_today()

    # --- term window -------------------------------------------------------
    if contract.end_date and ref_date > contract.end_date:
        findings.append(
            {
                "type": COMPLIANCE_EXCEPTION_TYPE,
                "severity": "warning",
                "message": (
                    f"Invoice dated {ref_date.isoformat()} is after contract "
                    f"{contract.contract_number} expired ({contract.end_date.isoformat()})"
                ),
            }
        )
    if contract.start_date and ref_date < contract.start_date:
        findings.append(
            {
                "type": COMPLIANCE_EXCEPTION_TYPE,
                "severity": "warning",
                "message": (
                    f"Invoice dated {ref_date.isoformat()} predates contract "
                    f"{contract.contract_number} start ({contract.start_date.isoformat()})"
                ),
            }
        )

    # --- terminated / cancelled contract -----------------------------------
    if contract.status in (ContractStatus.terminated, ContractStatus.cancelled):
        findings.append(
            {
                "type": COMPLIANCE_EXCEPTION_TYPE,
                "severity": "warning",
                "message": (
                    f"Spend recorded against {contract.status} contract {contract.contract_number}"
                ),
            }
        )

    # --- vendor mismatch ---------------------------------------------------
    if invoice.vendor_id and contract.vendor_id and invoice.vendor_id != contract.vendor_id:
        findings.append(
            {
                "type": COMPLIANCE_EXCEPTION_TYPE,
                "severity": "warning",
                "message": (
                    f"Invoice vendor does not match contract {contract.contract_number} vendor"
                ),
            }
        )

    # --- cumulative spend over limit --------------------------------------
    # Skip entirely when the invoice's own currency differs from the
    # contract's — it can't be validly added to a same-currency prior sum
    # and compared against a limit denominated in yet another currency.
    if (
        contract.spend_limit is not None
        and invoice.amount is not None
        and (invoice.currency or "USD") == contract.currency
    ):
        prior = (
            await db.execute(
                select(func.coalesce(func.sum(Invoice.amount), 0)).where(
                    Invoice.contract_id == contract.id,
                    Invoice.id != invoice.id,
                    Invoice.status != InvoiceStatus.rejected,
                    Invoice.currency == contract.currency,
                )
            )
        ).scalar()
        cumulative = Decimal(prior or 0) + invoice.amount
        if cumulative > contract.spend_limit:
            findings.append(
                {
                    "type": COMPLIANCE_EXCEPTION_TYPE,
                    "severity": "error" if contract.not_to_exceed else "warning",
                    "message": (
                        f"Cumulative spend {cumulative} exceeds contract "
                        f"{contract.contract_number} limit {contract.spend_limit}"
                        + (" (not-to-exceed)" if contract.not_to_exceed else "")
                    ),
                }
            )

    # --- GL outside contract terms ----------------------------------------
    allowed_gl = (contract.terms or {}).get("allowed_gl_accounts")
    if allowed_gl and invoice.gl_account and invoice.gl_account not in allowed_gl:
        findings.append(
            {
                "type": COMPLIANCE_EXCEPTION_TYPE,
                "severity": "warning",
                "message": (
                    f"GL account {invoice.gl_account} is outside contract "
                    f"{contract.contract_number} allowed accounts"
                ),
            }
        )

    return findings
