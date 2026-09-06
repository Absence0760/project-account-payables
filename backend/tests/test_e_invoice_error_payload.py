"""The structured 422 body for e-invoice validation failures.

`FieldError` has always carried three things — the field path, a reason code,
and a PII-free human sentence — but `EInvoiceValidationError.__str__` emits only
the first two, so every HTTP client had to keep its own code→prose map to say
anything a person could act on. `error_payload` is the fix: it returns all three
in FastAPI's own validation-error item shape (`loc` / `type` / `msg`).

What these tests pin, in order of what would hurt most if it broke:

1. **PII stays out.** The payload is an HTTP error body; a field VALUE reaching
   it would leak a tax id / address / amount to a log or a browser.
2. **Rule ids survive.** The BIS Billing 3.0 conformance pass reports the EN
   16931 rule id AS the code (`BR-CO-09`), and that identifier is what a
   receiving Access Point's validator names. A client that flattens the list to
   a string keeps only `loc` + `msg`, so the rule id is folded into `msg` too.
3. **`str(exc)` is unchanged.** The inbound PEPPOL receive path and the einvoice
   extraction adapter log it; migrating the HTTP body must not move the logs.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.services.e_invoice import (
    EInvoiceValidationError,
    FieldError,
    error_payload,
    validate_document,
)
from app.services.e_invoice.bis3 import bis3_conformance_errors
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)


def _payload_strings(payload: list[dict]) -> str:
    """Everything in the payload, flattened — what a leak check must search."""
    return repr(payload)


def test_payload_carries_field_code_and_message():
    payload = error_payload(
        [FieldError("seller.tax_id", "malformed", "Seller tax id format is invalid for country")]
    )
    assert payload == [
        {
            "loc": ["seller.tax_id"],
            "msg": "Seller tax id format is invalid for country",
            "type": "malformed",
        }
    ]


def test_generic_codes_do_not_prefix_the_message():
    """`missing` / `malformed` / `inconsistent` / `implausible` name a KIND of
    problem the sentence already spells out — prefixing would stutter."""
    for code in ("missing", "malformed", "inconsistent", "implausible"):
        (item,) = error_payload([FieldError("currency", code, "Document currency is required")])
        assert item["msg"] == "Document currency is required"
        assert item["type"] == code


def test_rule_id_code_is_folded_into_the_message():
    """A client that flattens the list keeps `loc` + `msg` only. The rule id is
    the half a receiving Access Point's validator names, so it must ride the
    message as well as `type`."""
    (item,) = error_payload(
        [
            FieldError(
                "seller.tax_id",
                "BR-CO-09",
                "VAT identifier must start with the ISO 3166-1 country prefix that issued it",
            )
        ]
    )
    assert item["type"] == "BR-CO-09"
    assert item["msg"].startswith("BR-CO-09: ")
    assert "ISO 3166-1" in item["msg"]


def test_rule_id_already_in_the_message_is_not_repeated():
    """The mandatory-element checks keep `code == "missing"` and put the rule id
    at the front of the message themselves (`BR-02: an invoice needs its
    number`). Nothing to fold in, and nothing doubled."""
    (item,) = error_payload(
        [FieldError("invoice_number", "missing", "BR-02: an invoice needs its number")]
    )
    assert item["msg"] == "BR-02: an invoice needs its number"

    # And a rule-id code whose message already leads with it stays as-is.
    (same,) = error_payload([FieldError("due_date", "BR-CO-25", "BR-CO-25: a due date is needed")])
    assert same["msg"] == "BR-CO-25: a due date is needed"


def _pii_document() -> EInvoiceDocument:
    """A document whose every field carries a distinctive, findable value, and
    which fails both the structural/tax pass and the BIS3 conformance pass."""
    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number="",  # missing → structural failure
        issue_date=date(2026, 1, 1),
        currency="EURO",  # malformed
        seller=EInvoiceParty(
            name="Secret Vendor GmbH",
            tax_id="DE99",  # malformed for DE
            address_lines=["13 Confidential Strasse"],
            city="Geheimstadt",
            postal_code="99999",
            country_code="DE",
        ),
        buyer=EInvoiceParty(
            name="Our Co",
            tax_id="FR777777777",
            address_lines=["1 Buyer Lane"],
            city="Buyertown",
            postal_code="11111",
            country_code="FR",
        ),
        lines=[
            EInvoiceLine(
                line_id="",
                description="",
                quantity=None,
                unit_price=None,
                line_total=Decimal("31337.42"),
            )
        ],
        taxes=[EInvoiceTax(category="", rate=Decimal("77.00"), taxable_amount=None)],
        tax_exclusive_amount=Decimal("31337.42"),
        tax_inclusive_amount=Decimal("99999.99"),
        tax_total=Decimal("1.00"),
        payable_amount=Decimal("99999.99"),
    )


def test_payload_is_pii_free_across_both_validation_passes():
    """The whole point of the field/code/message split: a value can be objected
    to without being repeated. Verified, not assumed — this body goes to a
    browser and to whatever the client logs."""
    doc = _pii_document()
    errors = validate_document(doc) + bis3_conformance_errors(doc)
    assert errors, "fixture must actually fail validation"

    blob = _payload_strings(error_payload(errors))
    for value in (
        "Secret Vendor GmbH",
        "DE99",
        "13 Confidential Strasse",
        "Geheimstadt",
        "99999",
        "FR777777777",
        "31337.42",
        "99999.99",
        "77.00",
        "EURO",
    ):
        assert value not in blob, f"{value!r} leaked into the 422 body"


def test_bis3_rule_ids_reach_the_payload():
    """The conformance pass's identifiers survive serialization — both as the
    machine-readable `type` and inside the human `msg`."""
    doc = _pii_document()
    payload = error_payload(bis3_conformance_errors(doc))

    types = {item["type"] for item in payload}
    rule_ids = {t for t in types if t.upper() == t and t.startswith(("BR-", "PEPPOL-"))}
    assert rule_ids, f"expected at least one EN 16931 rule id, got {types}"

    for item in payload:
        if item["type"] in rule_ids:
            assert item["type"] in item["msg"]


def test_exception_payload_matches_the_function():
    errors = [FieldError("lines", "missing", "At least one invoice line is required")]
    assert EInvoiceValidationError(errors).payload == error_payload(errors)


def test_str_rendering_is_unchanged():
    """`str(exc)` is the LOGGING contract (peppol_receive, the einvoice
    extraction adapter). The HTTP body moved; the log line did not."""
    exc = EInvoiceValidationError(
        [
            FieldError("invoice_number", "missing", "Invoice number is required"),
            FieldError("seller.tax_id", "BR-CO-09", "VAT identifier must be country-prefixed"),
        ]
    )
    assert str(exc) == "invoice_number: missing; seller.tax_id: BR-CO-09"


def test_no_message_contains_the_join_separator():
    """The frontend re-splits the flattened rendering (`"field: msg; field: msg"`)
    of this payload back into rows — `formatApiDetail` joins with `'; '`, and
    `ApiError` carries only a message, so that string is all a browser client
    gets. A message containing `'; '` would silently split into two half-rows.
    Cheaper to assert than to make the client parser tolerant of it."""
    doc = _pii_document()
    errors = validate_document(doc) + bis3_conformance_errors(doc)
    for item in error_payload(errors):
        assert "; " not in item["msg"], item
        # The field path must also stay free of the two delimiters, or the
        # split can't find the boundary.
        assert "; " not in item["loc"][0] and ": " not in item["loc"][0]
