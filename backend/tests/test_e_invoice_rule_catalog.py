"""The e-invoice rule catalogue, and the drift check built on it.

`docs/decisions.md` §95 deleted the frontend's hand-written code→prose map when
the 422 became structured, and named the durable replacement: a code→message-key
map **generated** from the backend's own rule set. A generator on its own would
just recreate that map by hand once a year — the guard is what makes it durable,
so these tests pin BOTH halves:

  * the catalogue is complete (nothing the validators can emit is missing), and
  * ``--check`` actually goes red when the rule set gains a code.

The rest of the chain is guarded on the frontend side: the generated file is
``satisfies Record<string, MessageKey>``, so a key ``en.ts`` lacks is a
``pnpm check`` error, and ``messages_parity.test.ts`` then demands the other
five locales.
"""

from __future__ import annotations

import textwrap
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.e_invoice.bis3 import bis3_conformance_errors
from app.services.e_invoice.codelists import VAT_CATEGORY_RULE_INFIX, vat_category_rule
from app.services.e_invoice.model import (
    EInvoiceDocument,
    EInvoiceFormat,
    EInvoiceLine,
    EInvoiceParty,
    EInvoiceTax,
)
from app.services.e_invoice.rule_catalog import (
    indirect_code_modules,
    opaque_rule_codes,
    returned_string_literals,
    rule_codes,
)
from app.services.e_invoice.tax_rules import validate_tax_document
from app.services.e_invoice.validate import (
    GENERIC_ERROR_CODES,
    is_rule_id,
    validate_document,
)

# ---------------------------------------------------------------------------
# The catalogue itself.
# ---------------------------------------------------------------------------


def test_every_code_is_a_rule_id_or_a_declared_generic_kind():
    """No third category — the 422 renders exactly these two ways."""
    for rc in rule_codes():
        assert rc.client_visible == is_rule_id(rc.code), rc.code
        if not rc.client_visible:
            assert rc.code in GENERIC_ERROR_CODES, (
                f"{rc.code!r} is neither a rule id nor a declared generic kind"
            )


def test_the_generic_kinds_are_exactly_the_opaque_half():
    assert sorted(rc.code for rc in opaque_rule_codes()) == sorted(GENERIC_ERROR_CODES)


def test_the_indirect_modules_only_ever_return_declared_generic_kinds():
    """The one hole a source scan cannot see, closed by a second scan.

    ``tax_rules`` builds its ``FieldError`` from a code held in a LOCAL — the
    return value of ``validate_tax_id`` / ``validate_tax_rate`` — so the code
    literal is nowhere near the call. `GENERIC_ERROR_CODES` covers it, and this
    is what stops that declaration going stale: a new ``return "something"`` in
    such a module fails here until the vocabulary is extended.
    """
    assert indirect_code_modules() == ("tax_rules",), (
        "a new module builds a FieldError from a variable — check its codes are "
        "in GENERIC_ERROR_CODES, then update this pin"
    )
    for module in indirect_code_modules():
        for literal in returned_string_literals(module):
            assert literal in GENERIC_ERROR_CODES, (
                f"{module} returns {literal!r}, which no catalogue entry covers"
            )


def test_the_per_category_families_are_expanded_over_every_infix():
    known = {rc.code for rc in rule_codes()}
    for suffix in ("01", "05", "08"):
        for category, infix in VAT_CATEGORY_RULE_INFIX.items():
            code = vat_category_rule(category, suffix)
            assert code == f"BR-{infix}-{suffix}"
            assert code in known, f"{code} is emittable but uncatalogued"


def test_a_literal_beats_the_family_expansion():
    """BR-S-05 is the MIRROR of the -05 family, not a member of it.

    The family says "this category requires a ZERO rate"; the literal says a
    standard-rated line requires one ABOVE zero. Collapsing them would render
    the opposite rule to a supplier.
    """
    by_code = {rc.code: rc for rc in rule_codes()}
    assert by_code["BR-S-05"].family is None
    assert "above zero" in by_code["BR-S-05"].message
    assert by_code["BR-Z-05"].family == "05"


# ---------------------------------------------------------------------------
# Completeness, driven rather than scanned: run the validators over documents
# engineered to fail and assert nothing they emit is outside the catalogue.
# ---------------------------------------------------------------------------


def _broken_document() -> EInvoiceDocument:
    """A document that trips the code-list, calculation and completeness passes."""
    return EInvoiceDocument(
        source_format=EInvoiceFormat.UBL,
        invoice_number="INV-1",
        issue_date=date(2026, 1, 31),
        currency="XXQ",  # not ISO 4217 → BR-CL-03
        invoice_type_code="999",  # not UNTDID 1001 → BR-CL-01
        payment_means_code="!!",  # not UNCL4461-shaped → BR-CL-16
        seller=EInvoiceParty(name="S", country_code="ZZ", tax_id="123456"),
        buyer=EInvoiceParty(name="B", country_code="ZZ"),
        lines=[
            EInvoiceLine(
                line_id="1",
                description="d",
                quantity=Decimal("2"),
                unit_price=Decimal("10.00"),
                line_total=Decimal("999.00"),  # ≠ qty × price → R120
                unit_code="!!",  # → BR-CL-23
                tax_category="Z",
                tax_rate=Decimal("7.00"),  # zero-rate category, non-zero → BR-Z-05
            )
        ],
        taxes=[
            EInvoiceTax(
                category="QQ",  # not UNCL5305 → BR-CL-17
                rate=Decimal("7.00"),
                taxable_amount=Decimal("1.00"),
                tax_amount=Decimal("500.00"),  # ≠ base × rate → BR-CO-17
            )
        ],
        line_extension_amount=Decimal("1.00"),
        tax_exclusive_amount=Decimal("2.00"),
        tax_inclusive_amount=Decimal("3.00"),
        tax_total=Decimal("4.00"),
        payable_amount=Decimal("5.00"),
    )


def _empty_document() -> EInvoiceDocument:
    """Nothing populated — trips every presence rule the structural + BIS 3.0
    passes own."""
    return EInvoiceDocument(source_format=EInvoiceFormat.UBL)


def test_no_pass_emits_a_code_outside_the_catalogue():
    known = {rc.code for rc in rule_codes()}
    doc = _broken_document()
    emitted = {
        e.code
        for e in (
            *validate_document(doc, check_tax=False),
            *validate_tax_document(doc),
            # `bis3_conformance_errors` composes the EN 16931 code-list and
            # calculation passes, so this reaches every rule module.
            *bis3_conformance_errors(doc),
            # An empty document is the other extreme — every presence rule.
            *validate_document(_empty_document(), check_tax=True),
            *bis3_conformance_errors(_empty_document()),
        )
    }
    assert emitted, "the fixtures stopped tripping anything — they are the coverage"
    assert emitted <= known, f"uncatalogued code(s): {sorted(emitted - known)}"


# ---------------------------------------------------------------------------
# The drift check bites. This is the point of the whole slice: a generator with
# no failing guard is the hand-written map §95 threw away, re-created.
# ---------------------------------------------------------------------------


def _generator():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "scripts" / "gen_einvoice_rule_messages.py"
    spec = importlib.util.spec_from_file_location("gen_einvoice_rule_messages", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_catalogue_is_in_sync():
    """The same assertion CI's `Backend lint` job makes."""
    assert _generator().main(["--check"]) == 0


def test_the_check_goes_red_when_the_rule_set_gains_a_code(tmp_path, monkeypatch):
    """Append a rule to a COPY of the package, and watch `--check` refuse it.

    A copy rather than the real file: mutating `en16931_rules.py` in-process
    would leave the tree dirty if the test failed halfway, and `rule_codes()`
    reads the package directory, so pointing it at a copy is the whole change.
    """
    import app.services.e_invoice.rule_catalog as catalog

    package = Path(catalog.__file__).resolve().parent
    sandbox = tmp_path / "e_invoice"
    sandbox.mkdir()
    for src in package.glob("*.py"):
        (sandbox / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    baseline = {rc.code for rc in rule_codes()}
    assert "BR-CO-99" not in baseline

    (sandbox / "en16931_rules.py").write_text(
        (sandbox / "en16931_rules.py").read_text(encoding="utf-8")
        + textwrap.dedent(
            """

            def _drift_probe():
                return _err("buyer.name", "BR-CO-99", "A rule the catalogue has never seen")
            """
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(catalog, "_PACKAGE_ROOT", sandbox)
    catalog.rule_codes.cache_clear()
    try:
        assert "BR-CO-99" in {rc.code for rc in catalog.rule_codes()}
        # The committed file predates the new rule, so the check must refuse it.
        assert _generator().main(["--check"]) == 1
    finally:
        # Undo BEFORE the last assertion, and clear the cache on both sides of
        # it: `rule_codes` is `lru_cache`d, so a read taken while the sandbox is
        # still installed would poison every later test in the process.
        monkeypatch.undo()
        catalog.rule_codes.cache_clear()

    # …and the catalogue is unchanged once the probe is gone.
    assert {rc.code for rc in rule_codes()} == baseline


def test_the_generator_refuses_to_key_an_opaque_code():
    """A generic kind has no channel to the client, so it must not get a key.

    Mapping one would produce a message nothing can ever select — the dead-key
    failure mode a "just add every code" generator falls into.
    """
    gen = _generator()
    rendered = gen.render()
    for rc in opaque_rule_codes():
        assert f"'{rc.code}': 'invoices." not in rendered
        assert f"\t'{rc.code}'," in rendered  # listed, not silently dropped


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("BR-CO-09", "invoices.modal.einvoice.rule.brCo09"),
        ("PEPPOL-EN16931-R120", "invoices.modal.einvoice.rule.peppolEn16931R120"),
    ],
)
def test_message_key_derivation_is_deterministic(code, expected):
    gen = _generator()
    rc = next(r for r in rule_codes() if r.code == code)
    assert gen.message_key(rc) == expected
