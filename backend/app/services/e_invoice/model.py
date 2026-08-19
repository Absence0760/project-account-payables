"""Normalized e-invoice document model.

A single, format-neutral representation that both inbound parsers (UBL 2.1
and UN/CEFACT CII) map *into* and a future outbound generator can render
*out of* — so the next slice can emit UBL 2.1 from the same shape without
remodeling.

All monetary and quantity fields are ``Decimal | None`` (never ``float``) to
honour the project's "money is exact" invariant. PII-bearing fields (tax ids,
addresses, email) carry a comment so they're never logged or surfaced in
error messages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum


class EInvoiceFormat(StrEnum):
    """The XML dialect a document was parsed from."""

    UBL = "ubl"
    CII = "cii"


@dataclass
class EInvoiceParty:
    name: str | None = None
    tax_id: str | None = None  # VAT / company tax id (PII — never logged)
    registration_id: str | None = None  # legal/company registration number
    address_lines: list[str] = field(default_factory=list)  # PII — never logged
    city: str | None = None
    postal_code: str | None = None
    country_code: str | None = None  # ISO 3166-1 alpha-2
    email: str | None = None
    # BT-34 (seller) / BT-49 (buyer) "electronic address" - the party's PEPPOL
    # participant id. UBL carries it as `cac:Party/cbc:EndpointID` with the EAS
    # code on `@schemeID`; PEPPOL BIS Billing 3.0 makes BOTH parties' addresses
    # mandatory, so a document without them cannot claim the profile.
    endpoint_id: str | None = None  # the registered id (often the VAT/org id)
    endpoint_scheme_id: str | None = None  # EAS code, e.g. "9930"


@dataclass
class EInvoiceTax:
    category: str | None = None  # e.g. "S", "Z", "E"
    rate: Decimal | None = None  # percent, e.g. Decimal("19.00")
    taxable_amount: Decimal | None = None
    tax_amount: Decimal | None = None


@dataclass
class EInvoiceLine:
    line_id: str | None = None
    item_code: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit_code: str | None = None  # UN/ECE unit (e.g. "C62", "HUR")
    unit_price: Decimal | None = None
    line_total: Decimal | None = None  # line extension amount
    tax_rate: Decimal | None = None  # BT-152, percent
    tax_amount: Decimal | None = None
    # BT-151 - the invoiced item's VAT category code (UNCL5305: "S", "Z", "E",
    # ...). UBL carries it on `cac:Item/cac:ClassifiedTaxCategory/cbc:ID`, which
    # EN 16931 requires on every line; without it a line states no VAT
    # treatment at all.
    tax_category: str | None = None


@dataclass
class EInvoiceDocument:
    source_format: EInvoiceFormat
    invoice_number: str | None = None
    issue_date: date | None = None
    due_date: date | None = None
    currency: str | None = None  # ISO 4217, 3-char upper
    invoice_type_code: str | None = None  # UNCL1001, e.g. "380"
    buyer_reference: str | None = None  # maps to reference_number
    order_reference: str | None = None  # maps to po_number
    payment_terms_note: str | None = None
    payment_means_code: str | None = None  # UNCL4461 → mapped to payment_method downstream
    seller: EInvoiceParty = field(default_factory=EInvoiceParty)
    buyer: EInvoiceParty = field(default_factory=EInvoiceParty)
    line_extension_amount: Decimal | None = None  # subtotal (net)
    tax_exclusive_amount: Decimal | None = None
    tax_inclusive_amount: Decimal | None = None  # grand total → amount
    tax_total: Decimal | None = None  # → tax_amount
    allowance_total: Decimal | None = None  # → discount_amount
    charge_total: Decimal | None = None  # → shipping/charges
    payable_amount: Decimal | None = None  # authoritative grand total
    taxes: list[EInvoiceTax] = field(default_factory=list)
    lines: list[EInvoiceLine] = field(default_factory=list)
    raw_xml_root_tag: str | None = None  # for debugging / raw_response
