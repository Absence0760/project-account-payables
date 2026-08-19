"""PEPPOL BIS Billing 3.0 conformance — the claim, and what backs it.

``CustomizationID`` and ``ProfileID`` are not decoration: together they are the
document's **assertion** that it conforms to EN 16931 as profiled by PEPPOL BIS
Billing 3.0. An Access Point routes on them and validates against that profile's
Schematron. Emitting them on a document that does not meet the profile is worse
than emitting nothing — it turns a document the receiver could have handled as
plain UBL into one it must reject, and it makes our own logs claim a compliance
posture we do not have.

So this module holds two things, and `generate.generate_ubl` uses both:

1. the two profile identifiers, and
2. :func:`bis3_conformance_errors` — the mandatory-element check that decides
   whether we are entitled to declare them.

**Scope, stated honestly.** This is a *mandatory-element* check, not the
official Schematron. It covers the BIS/EN 16931 business rules whose inputs the
normalized :class:`EInvoiceDocument` actually carries — the ones that were
missing when this was written (no endpoint ids on either party, tax subtotals
without their amounts, invoice lines with no VAT category) plus the core
required fields. It does not evaluate the calculation rules (BR-CO-*), code-list
membership, or anything needing data the model has no slot for. A document that
passes here can still fail the official validator; a document that FAILS here
provably does not conform, which is what makes the conditional declaration
sound. Vendoring the official Schematron and running it in CI is the next rung
and is tracked in ``docs/followups.md``.

PII invariant: every :class:`FieldError` names the field path and a generic
code, never a value — identical to :mod:`app.services.e_invoice.validate`.
"""

from __future__ import annotations

from app.services.e_invoice.model import EInvoiceDocument, EInvoiceParty
from app.services.e_invoice.validate import EInvoiceValidationError, FieldError

# The two profile identifiers, verbatim from PEPPOL BIS Billing 3.0.
#
# They are NOT free strings: `BIS3_CUSTOMIZATION_ID` is the same customization
# id embedded in the AS4 document-type id the send path already declares
# (`peppol_adapters.constants.PEPPOL_BIS_BILLING_DOCTYPE`), and
# `BIS3_PROFILE_ID` is exactly that path's process id
# (`PEPPOL_BIS_BILLING_PROCESSID`). They are duplicated here rather than
# imported to keep the `e_invoice` package free of a dependency on the PEPPOL
# adapter package (which imports `e_invoice`); `tests/test_e_invoice_bis3.py`
# is the drift guard that pins them to those constants.
BIS3_CUSTOMIZATION_ID = "urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0"
BIS3_PROFILE_ID = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"


def _party_errors(prefix: str, party: EInvoiceParty) -> list[FieldError]:
    errors: list[FieldError] = []
    if not party.name:
        errors.append(FieldError(f"{prefix}.name", "missing", "BIS 3.0 requires the party name"))
    # BT-34 (seller) / BT-49 (buyer) electronic address. PEPPOL-EN16931-R020 /
    # R010 make both mandatory, and the EAS scheme id is part of the address —
    # an endpoint value with no scheme identifies nothing.
    if not party.endpoint_id:
        errors.append(
            FieldError(
                f"{prefix}.endpoint_id",
                "missing",
                "BIS 3.0 requires the party's electronic address (EndpointID)",
            )
        )
    elif not party.endpoint_scheme_id:
        errors.append(
            FieldError(
                f"{prefix}.endpoint_scheme_id",
                "missing",
                "The electronic address requires its EAS scheme identifier",
            )
        )
    # BR-09 / BR-11 — each party's postal address must carry a country code.
    if not party.country_code:
        errors.append(
            FieldError(
                f"{prefix}.country_code",
                "missing",
                "BIS 3.0 requires the party's country code",
            )
        )
    return errors


def bis3_conformance_errors(doc: EInvoiceDocument) -> list[FieldError]:
    """Mandatory-element check for PEPPOL BIS Billing 3.0. Empty list = passes.

    Pure: no IO, no clock. See the module docstring for exactly what this does
    and does not cover.
    """
    errors: list[FieldError] = []

    if not doc.invoice_number:
        errors.append(FieldError("invoice_number", "missing", "BIS 3.0 requires the invoice id"))
    if doc.issue_date is None:
        errors.append(FieldError("issue_date", "missing", "BIS 3.0 requires the issue date"))
    if not doc.currency:
        errors.append(
            FieldError("currency", "missing", "BIS 3.0 requires the document currency code")
        )

    errors.extend(_party_errors("seller", doc.seller))
    errors.extend(_party_errors("buyer", doc.buyer))

    # BR-CO-10 / BR-CO-13 / BR-CO-15 / BR-15 — the four monetary totals.
    for field_name in (
        "line_extension_amount",
        "tax_exclusive_amount",
        "tax_inclusive_amount",
        "payable_amount",
    ):
        if getattr(doc, field_name) is None:
            errors.append(
                FieldError(
                    field_name,
                    "missing",
                    "BIS 3.0 requires this LegalMonetaryTotal amount",
                )
            )

    # BR-CO-14 — the document tax total, and one complete VAT breakdown per
    # category. A `cac:TaxSubtotal` needs BOTH amounts and its category; the
    # generator used to emit one carrying only `TaxCategory/Percent`.
    if doc.tax_total is None:
        errors.append(FieldError("tax_total", "missing", "BIS 3.0 requires the document tax total"))
    if not doc.taxes:
        errors.append(FieldError("taxes", "missing", "BIS 3.0 requires a VAT breakdown"))
    for i, tax in enumerate(doc.taxes):
        if tax.taxable_amount is None:
            errors.append(
                FieldError(f"taxes[{i}].taxable_amount", "missing", "VAT breakdown needs its base")
            )
        if tax.tax_amount is None:
            errors.append(
                FieldError(f"taxes[{i}].tax_amount", "missing", "VAT breakdown needs its amount")
            )
        if not tax.category:
            errors.append(
                FieldError(
                    f"taxes[{i}].category", "missing", "VAT breakdown needs its category code"
                )
            )

    if not doc.lines:
        errors.append(FieldError("lines", "missing", "BIS 3.0 requires at least one invoice line"))
    for i, line in enumerate(doc.lines):
        if not line.line_id:
            errors.append(FieldError(f"lines[{i}].line_id", "missing", "Invoice line needs an id"))
        if line.quantity is None:
            errors.append(
                FieldError(f"lines[{i}].quantity", "missing", "Invoice line needs a quantity")
            )
        if line.line_total is None:
            errors.append(
                FieldError(f"lines[{i}].line_total", "missing", "Invoice line needs a net amount")
            )
        if line.unit_price is None:
            errors.append(
                FieldError(f"lines[{i}].unit_price", "missing", "Invoice line needs a price")
            )
        if not line.description:
            errors.append(
                FieldError(f"lines[{i}].description", "missing", "Invoice line needs an item name")
            )
        # BT-151 / BT-152 — the invoiced item's VAT category and rate
        # (`cac:Item/cac:ClassifiedTaxCategory`). Without them the line states
        # no VAT treatment at all.
        if not line.tax_category:
            errors.append(
                FieldError(
                    f"lines[{i}].tax_category",
                    "missing",
                    "Invoice line needs its VAT category code",
                )
            )
        if line.tax_rate is None:
            errors.append(
                FieldError(f"lines[{i}].tax_rate", "missing", "Invoice line needs its VAT rate")
            )

    return errors


def is_bis3_conformant(doc: EInvoiceDocument) -> bool:
    """True when :func:`bis3_conformance_errors` finds nothing."""
    return not bis3_conformance_errors(doc)


def assert_bis3_conformant(doc: EInvoiceDocument) -> None:
    """Raise :class:`EInvoiceValidationError` unless the document conforms.

    Used by the PEPPOL send path, which puts the document on the network under
    a document-type id that ASSERTS BIS Billing 3.0. ``str(exc)`` is the
    PII-free ``"field: code"`` join the route already returns as a 422.
    """
    errors = bis3_conformance_errors(doc)
    if errors:
        raise EInvoiceValidationError(errors)
