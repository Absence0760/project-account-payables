"""Structural validation of a normalized e-invoice (EN 16931 subset).

Validation is structural only — required fields present, currency well-formed,
at least one line, a grand total, and the core monetary identity. It does NOT
attempt full EN 16931 business-rule (BR-*) conformance.

PII invariant: a :class:`FieldError` names the *field path* and a generic
reason code — it NEVER embeds the field's value. So a malformed tax id,
address, or amount can be reported (``seller.tax_id: missing``) without the
value ever entering a log line or an HTTP error body.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.e_invoice.model import EInvoiceDocument

# Tolerance for the monetary identity check (rounding noise across emitters).
_TOLERANCE = Decimal("0.01")


@dataclass
class FieldError:
    field: str  # dotted path e.g. "seller.name", "payable_amount", "lines[2].quantity"
    code: str  # "missing" | "malformed" | "inconsistent"
    message: str  # names the FIELD only — never the value (no PII)


class EInvoiceValidationError(ValueError):
    """Raised when a parsed document fails structural validation.

    Carries the full :class:`FieldError` list. ``str(exc)`` is a PII-free
    ``"field: code"`` join suitable for logging / error responses.
    """

    def __init__(self, errors: list[FieldError]):
        self.errors = errors
        super().__init__("; ".join(f"{e.field}: {e.code}" for e in errors))


def validate_document(doc: EInvoiceDocument, *, check_tax: bool = True) -> list[FieldError]:
    """Return a list of problems; empty list means valid.

    Structural checks run first (required fields, currency, monetary identity).
    When ``check_tax`` is True (the default), country-specific tax rules from
    :mod:`app.services.e_invoice.tax_rules` are appended — the same PII-free
    :class:`FieldError` shape. Pass ``check_tax=False`` to get the historical
    structural-only behaviour (inbound parse callers can opt out).
    """
    errors: list[FieldError] = []

    if not doc.invoice_number:
        errors.append(FieldError("invoice_number", "missing", "Invoice number is required"))

    if doc.issue_date is None:
        errors.append(FieldError("issue_date", "missing", "Issue date is required"))

    if not doc.currency:
        errors.append(FieldError("currency", "missing", "Document currency is required"))
    elif not (len(doc.currency) == 3 and doc.currency.isalpha()):
        errors.append(
            FieldError("currency", "malformed", "Currency must be a 3-letter ISO 4217 code")
        )

    if not doc.seller.name:
        errors.append(FieldError("seller.name", "missing", "Seller name is required"))

    if not doc.buyer.name:
        errors.append(FieldError("buyer.name", "missing", "Buyer name is required"))

    if not doc.lines:
        errors.append(FieldError("lines", "missing", "At least one invoice line is required"))

    grand_total = doc.payable_amount if doc.payable_amount is not None else doc.tax_inclusive_amount
    if grand_total is None:
        errors.append(
            FieldError("payable_amount", "missing", "A grand total (payable amount) is required")
        )

    # Monetary identity: tax_inclusive == tax_exclusive + tax_total (when all present).
    if (
        doc.tax_inclusive_amount is not None
        and doc.tax_exclusive_amount is not None
        and doc.tax_total is not None
    ):
        expected = doc.tax_exclusive_amount + doc.tax_total
        if abs(doc.tax_inclusive_amount - expected) > _TOLERANCE:
            errors.append(
                FieldError(
                    "tax_inclusive_amount",
                    "inconsistent",
                    "Tax-inclusive total does not equal tax-exclusive total plus tax",
                )
            )

    if check_tax:
        # Imported lazily to avoid a circular import: tax_rules imports
        # FieldError from this module.
        from app.services.e_invoice.tax_rules import validate_tax_document

        errors.extend(validate_tax_document(doc))

    return errors


def assert_valid(doc: EInvoiceDocument, *, check_tax: bool = True) -> None:
    """Raise :class:`EInvoiceValidationError` if the document is invalid."""
    errors = validate_document(doc, check_tax=check_tax)
    if errors:
        raise EInvoiceValidationError(errors)
