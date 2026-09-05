"""Structural validation of a normalized e-invoice (EN 16931 subset).

Validation is structural only — required fields present, currency well-formed,
at least one line, a grand total, and the core monetary identity. It does NOT
attempt full EN 16931 business-rule (BR-*) conformance.

PII invariant: a :class:`FieldError` names the *field path* and a generic
reason code — it NEVER embeds the field's value. So a malformed tax id,
address, or amount can be reported (``seller.tax_id: missing``) without the
value ever entering a log line or an HTTP error body. The same invariant is
what makes :func:`error_payload` safe to return as an HTTP body.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.e_invoice.model import EInvoiceDocument

# Tolerance for the monetary identity check (rounding noise across emitters).
_TOLERANCE = Decimal("0.01")


@dataclass
class FieldError:
    field: str  # dotted path e.g. "seller.name", "payable_amount", "lines[2].quantity"
    code: str  # "missing" | "malformed" | "inconsistent"
    message: str  # names the FIELD only — never the value (no PII)


def _message_with_rule(error: FieldError) -> str:
    """The human message, guaranteed to name its rule id when it has one.

    Two kinds of ``code`` reach here. The structural + tax passes report a
    generic *kind* of problem (``missing`` / ``malformed`` / ``inconsistent``
    / ``implausible``), which the message already spells out in words. The
    EN 16931 / PEPPOL conformance pass reports the **rule id itself**
    (``BR-CO-09``, ``PEPPOL-EN16931-R120``) — the identifier a receiving
    Access Point's own validator will name — and its message does not repeat
    it. Folding the rule id into the message keeps it reaching a client that
    flattens :func:`error_payload` down to a string (ours does), instead of
    the rule id living only in a field such a client discards.

    A rule id is recognised by being upper-case; a generic code is not.
    """
    code = error.code
    if not code or code != code.upper() or not any(ch.isalpha() for ch in code):
        return error.message
    if error.message.startswith(f"{code}:"):
        return error.message
    return f"{code}: {error.message}"


def error_payload(errors: Sequence[FieldError]) -> list[dict[str, Any]]:
    """Serialize field errors as an HTTP 422 ``detail`` body.

    The shape is FastAPI/Pydantic's own validation-error item — ``loc`` /
    ``msg`` / ``type`` — deliberately, so a client that already renders a
    FastAPI 422 renders this one with no new code, and the HUMAN half of each
    error travels with the machine half. Before this, the routes returned
    ``str(exc)`` (``"field: code"``), which meant every client had to carry
    its own code→prose map to say anything a person could act on.

    - ``loc`` — the dotted field path, as a single element, so the standard
      renderer produces ``"seller.tax_id: …"``.
    - ``type`` — :attr:`FieldError.code`: a generic kind (``missing`` /
      ``malformed`` / ``inconsistent`` / ``implausible``) or an EN 16931 /
      PEPPOL rule id (``BR-CO-25``).
    - ``msg`` — the PII-free sentence, with the rule id folded in when the
      code is one (see :func:`_message_with_rule`).

    PII invariant: field, code and message never carry a field VALUE (module
    docstring), so nothing this returns can leak one.
    """
    return [{"loc": [e.field], "msg": _message_with_rule(e), "type": e.code} for e in errors]


class EInvoiceValidationError(ValueError):
    """Raised when a parsed document fails structural validation.

    Carries the full :class:`FieldError` list. ``str(exc)`` is a PII-free
    ``"field: code"`` join, kept for logging (the inbound PEPPOL receive path
    and the einvoice extraction adapter both log it). HTTP callers should use
    :attr:`payload` instead — same facts, plus the human message.
    """

    def __init__(self, errors: list[FieldError]):
        self.errors = errors
        super().__init__("; ".join(f"{e.field}: {e.code}" for e in errors))

    @property
    def payload(self) -> list[dict[str, Any]]:
        """This error as an HTTP 422 ``detail`` body — see :func:`error_payload`."""
        return error_payload(self.errors)


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
