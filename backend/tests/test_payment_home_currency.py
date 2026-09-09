"""One normaliser for `settings.payments.home_currency`.

The org's home currency is the denomination of `Payment.source_amount`, of
every money threshold in `settings.compliance`, and — critically — of the
comparison that decides whether an invoice needs an FX leg at all. It is a
free-text value in the tenant's own settings JSON; nothing validates it on
write.

**Four call sites normalised it four ways.** `compliance._home_currency`
stripped and upper-cased. `currency_conversion.resolve_reporting_currency`
stripped and upper-cased. `international_payments.prepare_international_payment`
only upper-cased what it was handed. And `api/payments._execute_single_payment`
— the site that decides the FX leg — only upper-cased too.

So a single trailing space was enough to route **every domestic payment**
through the international corridor: `"USD "` != `"USD"`, so a USD invoice
looked foreign, `pick_corridor` returned `international_wire` (SWIFT required,
FX required), and a domestic vendor with no SWIFT/BIC then failed the payment
outright — while the compliance gate, reading the same setting through its own
stripping helper, saw a perfectly ordinary domestic payment.

`international_payments.resolve_home_currency` is now the one owner. These
tests pin the behaviour, the agreement between every reader, and the fact that
a new reader has to be added deliberately.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment, PaymentRun
from app.models.vendor import Vendor
from app.services.compliance import _home_currency
from app.services.currency_conversion import resolve_reporting_currency
from app.services.fx_adapters.mock_adapter import MockFXAdapter
from app.services.international_payments import (
    DEFAULT_HOME_CURRENCY,
    normalize_currency_code,
    prepare_international_payment,
    resolve_home_currency,
)

APP_ROOT = Path(__file__).resolve().parents[1] / "app"

TENANT = "a"

# The exact defect: an admin pastes the code with a trailing space.
PADDED = "USD "


def _invoice(*, amount=Decimal("500.00"), currency="USD"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        amount=amount,
        currency=currency,
        correlation_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        entity_id=uuid.uuid4(),
        invoice_number="INV-HC",
        vendor_name="Domestic Vendor Inc",
        description=None,
    )


def _domestic_vendor():
    """A US vendor paid by ACH: routing + account, no IBAN and no SWIFT/BIC —
    which is exactly why a wrongly-selected international corridor turns into a
    hard failure rather than a merely-expensive payment."""
    return SimpleNamespace(
        bank_details={"routing_number": "021000021", "account_number": "12345678"},
        address_country="US",
    )


# ---------------------------------------------------------------------------
# The normaliser itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("USD", "USD"),
        (PADDED, "USD"),
        (" usd ", "USD"),
        ("\tEUR\n", "EUR"),
    ],
)
def test_resolve_home_currency_trims_and_upper_cases(raw, expected):
    assert resolve_home_currency({"payments": {"home_currency": raw}}) == expected


@pytest.mark.parametrize(
    "settings",
    [
        None,
        {},
        {"payments": None},
        {"payments": {}},
        {"payments": {"home_currency": "   "}},
        # Free-form tenant JSON: neither the settings blob nor the `payments`
        # block is schema-enforced, and a payment run must not 500 on either.
        ["not", "a", "dict"],
        {"payments": "USD"},
    ],
)
def test_resolve_home_currency_never_returns_blank(settings):
    """A blank code compares equal to nothing, which is how the FX leg got
    chosen for a domestic payment in the first place. Degrade to the platform
    default instead."""
    assert resolve_home_currency(settings) == DEFAULT_HOME_CURRENCY


@pytest.mark.parametrize("raw", [None, "", "   ", 3, {"a": 1}])
def test_normalize_currency_code_returns_none_for_anything_unusable(raw):
    assert normalize_currency_code(raw) is None


# ---------------------------------------------------------------------------
# The bug, at the orchestrator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_padded_home_currency_does_not_make_a_domestic_payment_international():
    """The regression, at `prepare_international_payment`.

    Before the fix, `source_currency = org_home_currency.upper()` left the
    trailing space in place, so `"USD " != "USD"` selected `international_wire`
    — which requires a SWIFT/BIC a domestic vendor does not have, so the
    payment did not merely take an expensive corridor, it FAILED.
    """
    fx = MockFXAdapter()

    prepared = await prepare_international_payment(
        invoice=_invoice(currency="USD"),
        vendor=_domestic_vendor(),
        org_home_currency=PADDED,
        fx_adapter=fx,
        requested_method="ach",
    )

    assert prepared.corridor.method == "ach"
    assert prepared.corridor.requires_fx is False
    assert prepared.corridor.requires_swift is False
    # No fabricated FX evidence on a payment that never crossed a currency.
    assert prepared.fx_rate is None
    assert prepared.payment.fx_rate is None
    assert prepared.payment.source_currency == "USD"
    assert prepared.payment.source_amount == Decimal("500.00")


@pytest.mark.asyncio
async def test_a_padded_invoice_currency_is_normalised_too():
    """The other half of the same comparison. Both sides go through the one
    normaliser, so neither can be the odd one out."""
    prepared = await prepare_international_payment(
        invoice=_invoice(currency=" usd "),
        vendor=_domestic_vendor(),
        org_home_currency="USD",
        fx_adapter=MockFXAdapter(),
        requested_method="ach",
    )
    assert prepared.corridor.method == "ach"
    assert prepared.payment.fx_rate is None


@pytest.mark.asyncio
async def test_a_genuinely_foreign_invoice_still_takes_the_fx_leg():
    """The fix must not blunt the check it repairs."""
    prepared = await prepare_international_payment(
        invoice=_invoice(currency="EUR", amount=Decimal("1000.00")),
        vendor=SimpleNamespace(
            bank_details={
                "iban": "DE89370400440532013000",
                "swift_bic": "DEUTDEFF",
                "country": "DE",
            },
            address_country=None,
        ),
        org_home_currency=PADDED,
        fx_adapter=MockFXAdapter({"mock_rates": {"EUR": "0.92"}}),
    )
    assert prepared.corridor.method == "international_wire"
    assert prepared.payment.fx_rate == Decimal("0.92")
    assert prepared.payment.source_currency == "USD"


# ---------------------------------------------------------------------------
# Every reader agrees
# ---------------------------------------------------------------------------


def test_every_reader_of_the_setting_agrees_on_a_padded_value():
    """The drift guard on the value itself.

    `compliance` denominates its KYC / AML thresholds in this code,
    `currency_conversion` uses it as the second rung of the reporting-currency
    chain, and `international_payments` decides the corridor with it. All three
    must resolve the same tenant setting to the same string, or a threshold is
    compared against an amount in a currency it was never denominated in.
    """
    org_settings = {"payments": {"home_currency": "  usd  "}}
    resolved = resolve_home_currency(org_settings)
    assert resolved == "USD"
    assert _home_currency(org_settings) == resolved
    assert resolve_reporting_currency(org_settings) == resolved


def test_reading_the_setting_out_of_the_json_has_a_declared_set_of_readers():
    """A new module that reaches into `settings.payments.home_currency` itself
    — rather than calling `resolve_home_currency` — is how the four
    normalisations accumulated. It has to be a deliberate edit here.
    """
    readers = {
        path.relative_to(APP_ROOT).as_posix()
        for path in sorted(APP_ROOT.rglob("*.py"))
        if re.search(r"""\.get\(\s*["']home_currency["']""", path.read_text())
    }
    assert readers == {
        # The owner.
        "services/international_payments.py",
        # The reporting-currency resolution chain reads it as its second rung,
        # for a different question ("what do we ROLL UP in?"), and normalises
        # identically — pinned by the agreement test above.
        "services/currency_conversion.py",
    }, f"undeclared reader(s) of settings.payments.home_currency: {sorted(readers)}"


# ---------------------------------------------------------------------------
# End to end, through the payment executor
# ---------------------------------------------------------------------------


def _user(uid):
    return SimpleNamespace(id=uid, full_name="Home Currency Tester", roles=["admin"])


@pytest.mark.asyncio
async def test_execute_does_not_route_a_domestic_payment_internationally(realdb):
    """The site the defect actually lived on: `_execute_single_payment` decided
    the FX leg by comparing an `.upper()`-only home currency against the
    invoice's. Requires the dev Postgres (`pnpm db:up`)."""
    from app.api.payments import _execute_single_payment
    from app.services.payment_adapters import get_payment_adapter

    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)

    inv_id, pay_id, run_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    corr = uuid.uuid4()
    async with mk() as s:
        # A real, clean vendor: the compliance gate runs on EVERY rail, and an
        # invoice with no screenable vendor holds — which would mask whichever
        # corridor was chosen behind an unrelated failure reason.
        vendor = Vendor(name="Domestic Vendor Inc", organization_id=info.org_id, status="active")
        s.add(vendor)
        await s.flush()
        s.add(
            Invoice(
                id=inv_id,
                invoice_number="INV-HOME-CUR",
                vendor_name="Domestic Vendor Inc",
                amount=Decimal("500.00"),
                currency="USD",
                status=InvoiceStatus.approved,
                organization_id=info.org_id,
                correlation_id=corr,
                vendor_id=vendor.id,
            )
        )
        s.add(
            PaymentRun(
                id=run_id,
                organization_id=info.org_id,
                status="executing",
                total_amount=Decimal("500.00"),
                initiated_by=info.users["ap_manager"],
            )
        )
        await s.flush()
        s.add(
            Payment(
                id=pay_id,
                invoice_id=inv_id,
                payment_run_id=run_id,
                amount=Decimal("500.00"),
                method="ach",
                status="pending",
                correlation_id=corr,
            )
        )
        await s.commit()

    org = SimpleNamespace(
        id=info.org_id,
        name="PyTest",
        slug="pytesta",
        # The trailing space is the whole test.
        settings={"payments": {"provider": "mock", "home_currency": PADDED}},
    )

    async with realdb.sessionmaker(TENANT)() as db:
        payment = (await db.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()
        await _execute_single_payment(
            db,
            payment=payment,
            org=org,
            adapter=get_payment_adapter({"provider": "mock"}),
            user=_user(info.users["admin"]),
            now=datetime.now(UTC),
        )
        await db.commit()

    async with mk() as s:
        settled = (await s.execute(select(Payment).where(Payment.id == pay_id))).scalar_one()

    # The international leg is decided BEFORE the compliance gate, so these
    # assertions hold whatever compliance then decides about the vendor.
    assert settled.method == "ach"
    assert settled.corridor is None
    assert settled.fx_rate is None
    assert settled.source_amount is None
    # Before the fix this was `international_payment_error: corridor
    # 'international_wire' requires a valid SWIFT/BIC …` — a domestic ACH
    # payment failed outright because of a stray space in a settings field.
    assert settled.failure_reason is None
