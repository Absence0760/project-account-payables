"""EN 16931 calculation rules (BR-CO-*) and code-list membership (BR-CL-*).

:mod:`app.services.e_invoice.bis3` used to check only that the mandatory
*elements* were present. That is half a conformance gate: a document can carry
every required field and still be arithmetically incoherent — a VAT breakdown
whose tax amount is not its base times its rate, a grand total that is not the
net plus the VAT, an invoice line whose net amount is not its quantity times its
price. The receiver's Schematron computes all of those, so a document that
passed our gate could still be rejected the moment it left the building, and our
generator would have stamped a BIS Billing 3.0 conformance claim on it first.

This module closes the substantive half of that gap **without vendoring the
official Schematron**: the rules are re-implemented directly, over the same
normalized :class:`EInvoiceDocument`, in the same pure style as the existing
checks. It does not make a pass a conformance guarantee (see
``backend/docs/e-invoicing.md`` § PEPPOL BIS Billing 3.0 conformance) — it makes
a much larger class of non-conformance provable before we claim otherwise.

**Money is Decimal end to end.** Every comparison quantizes both sides to two
decimals with ``ROUND_HALF_UP`` (the repo idiom — see
``services/billing/proration.py``) before subtracting; nothing is ever coerced
to ``float``. EN 16931 states its totals to two decimals, so the comparison
tolerance is one cent (``BR-CO-17`` carries the standard's own ±0.02 rounding
allowance).

**Rules the normalized model cannot answer**, and why — so the gap is recorded
rather than silently absent:

============  =============================================================
Rule          Why it is not evaluated
============  =============================================================
BR-CO-03      BT-7 / BT-8 (VAT point date + its code) have no model field.
BR-CO-05..08  Allowance/charge *reason* codes: the model carries only the
              document-level ``allowance_total`` / ``charge_total``, never
              the individual BG-20 / BG-21 groups they sum.
BR-CO-11/12   Same — the per-allowance and per-charge amounts they sum do
              not exist on the model.
BR-CO-19/20   BG-14 / BG-26 invoicing periods have no model field.
BR-CO-21..24  Allowance/charge and payment-instruction detail the model
              does not carry.
BR-CL-16/23   Payment means (UNCL4461) and unit of measure (UN/ECE Rec 20)
              get a *shape* check only — we hold no complete copy of either
              list. See :mod:`app.services.e_invoice.codelists`.
BR-CL-25      The CEF EAS scheme list is maintained outside the standard.
============  =============================================================

Two rules are evaluated in a **reduced** form the model makes exact:

* **BR-CO-16** — amount due = total with VAT − prepaid (BT-113) + rounding
  (BT-114). Neither addend has a model field, so the model cannot represent a
  part-paid invoice at all; the check therefore reduces to
  ``payable_amount == tax_inclusive_amount``, which is exactly what any
  document we can emit asserts.
* **PEPPOL-EN16931-R120** — line net = quantity x (net price / price base
  quantity). BT-149 (price base quantity) has no model field and ``ubl.py``
  does not parse it, so it is taken as 1. That is not a blind spot being
  papered over: if a source document priced per 100 units, our normalized copy
  has *lost* that, and re-emitting it would state the price per single unit —
  so flagging the arithmetic is the correct verdict for a document we cannot
  faithfully represent.

PII invariant: every :class:`FieldError` names the field path and the rule id,
never a value — identical to :mod:`app.services.e_invoice.validate`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from app.services.e_invoice.codelists import (
    ISO_3166_ALPHA2,
    ZERO_RATE_VAT_CATEGORIES,
    is_plausible_payment_means,
    is_plausible_unit_code,
    is_valid_country,
    is_valid_currency,
    is_valid_document_type,
    is_valid_vat_category,
    vat_category_rule,
)
from app.services.e_invoice.model import EInvoiceDocument, EInvoiceParty
from app.services.e_invoice.validate import FieldError

_CENTS = Decimal("0.01")
_ZERO = Decimal("0")

#: EN 16931 states its monetary totals to two decimals, so a one-cent gap is
#: the whole of the permitted rounding noise. Kept identical to
#: ``validate._TOLERANCE`` so the two guards can never disagree about the same
#: identity.
_TOLERANCE = Decimal("0.01")

#: BR-CO-17 carries its own published allowance of two cents, because the VAT
#: amount is a *derived* figure (base x rate) that emitters round differently.
_VAT_TOLERANCE = Decimal("0.02")

#: Greece issues VAT numbers under the ``EL`` prefix, which is not an ISO
#: 3166-1 code; BR-CO-09 names it explicitly as the one permitted exception.
_VAT_PREFIX_EXCEPTIONS = frozenset({"EL"})


def _q(value: Decimal) -> Decimal:
    """Quantize to 2dp, ROUND_HALF_UP — the repo's money idiom."""
    return value.quantize(_CENTS, rounding=ROUND_HALF_UP)


def _agrees(actual: Decimal, expected: Decimal, tolerance: Decimal = _TOLERANCE) -> bool:
    return abs(_q(actual) - _q(expected)) <= tolerance


def _or_zero(value: Decimal | None) -> Decimal:
    return _ZERO if value is None else value


def _err(field: str, rule: str, message: str) -> FieldError:
    """A rule failure. The rule id IS the ``code``, so the PII-free
    ``"field: code"`` join `EInvoiceValidationError` renders (and the 422 body
    the routes return) names the rule a receiver's validator would name."""
    return FieldError(field, rule, message)


# ---------------------------------------------------------------------------
# Code-list membership (BR-CL-*)
# ---------------------------------------------------------------------------


def _party_code_list_errors(prefix: str, party: EInvoiceParty) -> list[FieldError]:
    errors: list[FieldError] = []
    if not is_valid_country(party.country_code):
        errors.append(
            _err(
                f"{prefix}.country_code",
                "BR-CL-14",
                "Country code must be a member of ISO 3166-1 alpha-2",
            )
        )
    # BR-CO-09 — the party's VAT identifier must be prefixed with the ISO
    # 3166-1 alpha-2 code of the country that issued it (EL for Greece). Our
    # generator always emits `party.tax_id` under `cac:TaxScheme/cbc:ID = VAT`,
    # so in the document we actually produce this field IS BT-31 / BT-48 and
    # the rule applies unconditionally. A bare national id with no country
    # prefix (a US EIN, an AU ABN) genuinely cannot claim the profile.
    if party.tax_id:
        prefix_letters = party.tax_id.strip().upper().replace(" ", "")[:2]
        if prefix_letters not in ISO_3166_ALPHA2 and prefix_letters not in _VAT_PREFIX_EXCEPTIONS:
            errors.append(
                _err(
                    f"{prefix}.tax_id",
                    "BR-CO-09",
                    "VAT identifier must start with the ISO 3166-1 country prefix that issued it",
                )
            )
    return errors


def code_list_errors(doc: EInvoiceDocument) -> list[FieldError]:
    """BR-CL-* membership over every coded field the model carries.

    Absent values are NOT reported here — a code list says nothing about
    whether a field is required. :func:`bis3.bis3_conformance_errors` owns
    presence.
    """
    errors: list[FieldError] = []

    if not is_valid_currency(doc.currency):
        errors.append(
            _err(
                "currency",
                "BR-CL-03",
                "Document currency must be a member of ISO 4217 (alphabetic)",
            )
        )
    if not is_valid_document_type(doc.invoice_type_code):
        errors.append(
            _err(
                "invoice_type_code",
                "BR-CL-01",
                "Invoice type code must be a member of UNTDID 1001",
            )
        )
    if not is_plausible_payment_means(doc.payment_means_code):
        errors.append(
            _err(
                "payment_means_code",
                "BR-CL-16",
                "Payment means code is not shaped like a UNCL4461 code",
            )
        )

    errors.extend(_party_code_list_errors("seller", doc.seller))
    errors.extend(_party_code_list_errors("buyer", doc.buyer))

    for i, tax in enumerate(doc.taxes):
        if not is_valid_vat_category(tax.category):
            errors.append(
                _err(
                    f"taxes[{i}].category",
                    "BR-CL-17",
                    "VAT category code must be a member of the UNCL5305 EN 16931 subset",
                )
            )

    for i, line in enumerate(doc.lines):
        if not is_valid_vat_category(line.tax_category):
            errors.append(
                _err(
                    f"lines[{i}].tax_category",
                    "BR-CL-18",
                    "Invoiced item VAT category must be a member of the UNCL5305 EN 16931 subset",
                )
            )
        if not is_plausible_unit_code(line.unit_code):
            errors.append(
                _err(
                    f"lines[{i}].unit_code",
                    "BR-CL-23",
                    "Unit of measure is not shaped like a UN/ECE Rec 20 code",
                )
            )

    return errors


# ---------------------------------------------------------------------------
# Calculation rules (BR-CO-*, PEPPOL-EN16931-R120, per-category BR-<x>-*)
# ---------------------------------------------------------------------------


def _line_amount_errors(doc: EInvoiceDocument) -> list[FieldError]:
    """PEPPOL-EN16931-R120 — line net amount = quantity x item net price."""
    errors: list[FieldError] = []
    for i, line in enumerate(doc.lines):
        if line.quantity is None or line.unit_price is None or line.line_total is None:
            continue  # presence is bis3_conformance_errors' job, not this one's
        expected = line.quantity * line.unit_price
        if not _agrees(line.line_total, expected):
            errors.append(
                _err(
                    f"lines[{i}].line_total",
                    "PEPPOL-EN16931-R120",
                    "Invoice line net amount must equal quantity times item net price",
                )
            )
    return errors


def _document_total_errors(doc: EInvoiceDocument) -> list[FieldError]:
    """BR-CO-10 / 13 / 14 / 15 / 16 — the monetary totals must add up.

    Each rule is skipped when one of its own inputs is absent; the missing
    input is already reported by ``bis3_conformance_errors``, and reporting it
    twice under a calculation id would be noise.
    """
    errors: list[FieldError] = []

    # BR-CO-10 — Sum of invoice line net amounts.
    if doc.line_extension_amount is not None and doc.lines:
        if all(line.line_total is not None for line in doc.lines):
            line_sum = sum((line.line_total for line in doc.lines), _ZERO)
            if not _agrees(doc.line_extension_amount, line_sum):
                errors.append(
                    _err(
                        "line_extension_amount",
                        "BR-CO-10",
                        "Sum of invoice line net amounts must equal the total of the lines",
                    )
                )

    # BR-CO-13 — Invoice total without VAT = line total - allowances + charges.
    if doc.tax_exclusive_amount is not None and doc.line_extension_amount is not None:
        expected = (
            doc.line_extension_amount - _or_zero(doc.allowance_total) + _or_zero(doc.charge_total)
        )
        if not _agrees(doc.tax_exclusive_amount, expected):
            errors.append(
                _err(
                    "tax_exclusive_amount",
                    "BR-CO-13",
                    "Total without VAT must equal the line total less allowances plus charges",
                )
            )

    # BR-CO-14 — Invoice total VAT = sum of the VAT breakdown amounts.
    if doc.tax_total is not None and doc.taxes:
        if all(tax.tax_amount is not None for tax in doc.taxes):
            tax_sum = sum((tax.tax_amount for tax in doc.taxes), _ZERO)
            if not _agrees(doc.tax_total, tax_sum):
                errors.append(
                    _err(
                        "tax_total",
                        "BR-CO-14",
                        "Invoice total VAT must equal the sum of the VAT breakdown amounts",
                    )
                )

    # BR-CO-15 — Total with VAT = total without VAT + total VAT.
    if (
        doc.tax_inclusive_amount is not None
        and doc.tax_exclusive_amount is not None
        and doc.tax_total is not None
    ):
        expected = doc.tax_exclusive_amount + doc.tax_total
        if not _agrees(doc.tax_inclusive_amount, expected):
            errors.append(
                _err(
                    "tax_inclusive_amount",
                    "BR-CO-15",
                    "Total with VAT must equal the total without VAT plus the total VAT",
                )
            )

    # BR-CO-16 — Amount due = total with VAT (reduced form; see the module
    # docstring — BT-113 prepaid and BT-114 rounding have no model field).
    if doc.payable_amount is not None and doc.tax_inclusive_amount is not None:
        if not _agrees(doc.payable_amount, doc.tax_inclusive_amount):
            errors.append(
                _err(
                    "payable_amount",
                    "BR-CO-16",
                    "Amount due for payment must equal the total with VAT",
                )
            )

    return errors


def _vat_breakdown_errors(doc: EInvoiceDocument) -> list[FieldError]:
    """BR-CO-17 plus the per-category BR-<x>-01 / -05 / -08 family."""
    errors: list[FieldError] = []

    # BR-CO-17 — VAT category tax amount = taxable amount x rate / 100.
    for i, tax in enumerate(doc.taxes):
        if tax.taxable_amount is None or tax.rate is None or tax.tax_amount is None:
            continue
        expected = tax.taxable_amount * tax.rate / Decimal("100")
        if not _agrees(tax.tax_amount, expected, _VAT_TOLERANCE):
            errors.append(
                _err(
                    f"taxes[{i}].tax_amount",
                    "BR-CO-17",
                    "VAT category tax amount must equal its taxable amount times its rate",
                )
            )

    # BR-<x>-05 — a zero-rate / exempt / reverse-charge / out-of-scope line must
    # carry a rate of zero; a standard-rated one must carry a rate above zero.
    for i, line in enumerate(doc.lines):
        category = (line.tax_category or "").strip().upper()
        if not category or line.tax_rate is None:
            continue
        if category in ZERO_RATE_VAT_CATEGORIES and line.tax_rate != _ZERO:
            errors.append(
                _err(
                    f"lines[{i}].tax_rate",
                    vat_category_rule(category, "05"),
                    "This VAT category requires a zero rate",
                )
            )
        elif category == "S" and line.tax_rate <= _ZERO:
            errors.append(
                _err(
                    f"lines[{i}].tax_rate",
                    "BR-S-05",
                    "A standard-rated line requires a rate above zero",
                )
            )

    errors.extend(_breakdown_coverage_errors(doc))
    return errors


def _breakdown_coverage_errors(doc: EInvoiceDocument) -> list[FieldError]:
    """BR-<x>-01 and BR-<x>-08 — the VAT breakdown must cover, and agree with,
    the lines it summarises.

    BR-<x>-08 is **skipped whenever a document-level allowance or charge is
    present**: EN 16931 attributes each BG-20 / BG-21 group to a VAT category,
    and the model carries only the two undifferentiated totals, so the expected
    taxable amount per category is genuinely unknowable rather than zero.
    """
    errors: list[FieldError] = []
    if not doc.lines or not doc.taxes:
        return errors

    line_categories: set[str] = set()
    for line in doc.lines:
        category = (line.tax_category or "").strip().upper()
        if category:
            line_categories.add(category)

    breakdown_categories = {
        (tax.category or "").strip().upper() for tax in doc.taxes if (tax.category or "").strip()
    }

    # BR-<x>-01 — a line in a VAT category obliges a breakdown group for it.
    for category in sorted(line_categories - breakdown_categories):
        errors.append(
            _err(
                "taxes",
                vat_category_rule(category, "01"),
                "A VAT breakdown group is required for every VAT category used on a line",
            )
        )

    allowances_or_charges = _or_zero(doc.allowance_total) != _ZERO or (
        _or_zero(doc.charge_total) != _ZERO
    )
    if allowances_or_charges:
        return errors

    # BR-<x>-08 — the breakdown's taxable amount is the sum of the line net
    # amounts in that category. Only evaluated when every input is present.
    if any(line.line_total is None or not (line.tax_category or "").strip() for line in doc.lines):
        return errors

    per_category: dict[str, Decimal] = {}
    for line in doc.lines:
        key = (line.tax_category or "").strip().upper()
        per_category[key] = per_category.get(key, _ZERO) + line.line_total

    for i, tax in enumerate(doc.taxes):
        category = (tax.category or "").strip().upper()
        if not category or tax.taxable_amount is None:
            continue
        expected = per_category.get(category, _ZERO)
        if not _agrees(tax.taxable_amount, expected):
            errors.append(
                _err(
                    f"taxes[{i}].taxable_amount",
                    vat_category_rule(category, "08"),
                    "VAT category taxable amount must equal the sum of its invoice lines",
                )
            )

    return errors


def _document_completeness_errors(doc: EInvoiceDocument) -> list[FieldError]:
    """BR-CO-25 and BR-CO-26 — the two conditional rules the model can answer."""
    errors: list[FieldError] = []

    # BR-CO-25 — if there is money to pay, say when or on what terms.
    if doc.payable_amount is not None and doc.payable_amount > _ZERO:
        if doc.due_date is None and not (doc.payment_terms_note or "").strip():
            errors.append(
                _err(
                    "due_date",
                    "BR-CO-25",
                    "An invoice with an amount due requires a due date or payment terms",
                )
            )

    # BR-CO-26 — the buyer must be able to identify the seller automatically.
    # BT-29 (seller identifier) has no model field, so this reads BT-30
    # (registration id) and BT-31 (VAT id).
    if not (doc.seller.tax_id or doc.seller.registration_id):
        errors.append(
            _err(
                "seller.tax_id",
                "BR-CO-26",
                "The seller requires a legal registration or VAT identifier",
            )
        )

    return errors


def calculation_errors(doc: EInvoiceDocument) -> list[FieldError]:
    """Every EN 16931 calculation rule the normalized model can evaluate.

    Pure: no IO, no clock, no float. Empty list = nothing to object to (which
    is not the same as "conforms" — see the module docstring).
    """
    errors: list[FieldError] = []
    errors.extend(_line_amount_errors(doc))
    errors.extend(_document_total_errors(doc))
    errors.extend(_vat_breakdown_errors(doc))
    errors.extend(_document_completeness_errors(doc))
    return errors
