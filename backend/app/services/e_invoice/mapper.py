"""Map an ORM :class:`Invoice` (+ line items + our buyer identity) into the
normalized :class:`EInvoiceDocument` so it can be serialized to UBL 2.1.

Pure: no DB, no network. The route layer loads the rows and resolves the
buyer identity (our org / entity), then hands them here. The seller is the
*vendor* (the supplier who issued the invoice); the buyer is *us* (the
AccountingCustomerParty) — populated from :class:`BuyerIdentity`.

Money stays ``Decimal`` end to end — no field is ever coerced to ``float``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.invoice import Invoice, InvoiceLineItem
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)

_DEFAULT_INVOICE_TYPE_CODE = "380"  # UNCL1001 "Commercial invoice".


@dataclass
class BuyerIdentity:
    """The buyer (AccountingCustomerParty) — our org / entity identity.

    Lives here, not on the model: the mapper's job is to fill the existing
    ``EInvoiceDocument.buyer`` slot, so no model change is needed.
    """

    name: str
    tax_id: str | None = None
    registration_id: str | None = None
    address_lines: list[str] = field(default_factory=list)  # PII — never logged
    city: str | None = None
    postal_code: str | None = None
    country_code: str | None = None  # ISO-2
    email: str | None = None


def _split_address(address: str | None) -> list[str]:
    """Split a multi-line address string into address lines (one per line)."""
    if not address:
        return []
    return [line.strip() for line in address.splitlines() if line.strip()]


def invoice_to_einvoice_document(
    invoice: Invoice,
    line_items: list[InvoiceLineItem],
    buyer_identity: BuyerIdentity,
) -> EInvoiceDocument:
    """Build a normalized :class:`EInvoiceDocument` from an Invoice row.

    Seller = the vendor (from ``invoice.vendor_name`` / ``vendor_tax_id`` /
    ``vendor_address``). Buyer = ``buyer_identity`` (our org/entity).
    """
    seller = EInvoiceParty(
        name=invoice.vendor_name,
        tax_id=invoice.vendor_tax_id,
        address_lines=_split_address(invoice.vendor_address),
    )
    buyer = EInvoiceParty(
        name=buyer_identity.name,
        tax_id=buyer_identity.tax_id,
        registration_id=buyer_identity.registration_id,
        address_lines=list(buyer_identity.address_lines),
        city=buyer_identity.city,
        postal_code=buyer_identity.postal_code,
        country_code=buyer_identity.country_code,
        email=buyer_identity.email,
    )

    taxes: list[EInvoiceTax] = []
    if invoice.tax_amount is not None or invoice.tax_rate is not None:
        taxes.append(
            EInvoiceTax(
                category="S",
                rate=invoice.tax_rate,
                taxable_amount=invoice.subtotal,
                tax_amount=invoice.tax_amount,
            )
        )

    lines = [
        EInvoiceLine(
            line_id=str(li.line_number) if li.line_number is not None else None,
            item_code=li.item_code,
            description=li.description,
            quantity=li.quantity,
            unit_price=li.unit_price,
            line_total=li.total,
            tax_amount=li.tax,
        )
        for li in line_items
    ]

    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number=invoice.invoice_number,
        issue_date=invoice.invoice_date,
        due_date=invoice.due_date,
        currency=invoice.currency,
        invoice_type_code=_DEFAULT_INVOICE_TYPE_CODE,
        buyer_reference=invoice.reference_number,
        order_reference=invoice.po_number,
        payment_terms_note=invoice.payment_terms,
        payment_means_code=invoice.payment_method,
        seller=seller,
        buyer=buyer,
        line_extension_amount=invoice.subtotal,
        tax_exclusive_amount=invoice.subtotal,
        tax_inclusive_amount=invoice.amount,
        tax_total=invoice.tax_amount,
        allowance_total=invoice.discount_amount,
        charge_total=invoice.shipping_amount,
        payable_amount=invoice.amount,
        taxes=taxes,
        lines=lines,
    )
