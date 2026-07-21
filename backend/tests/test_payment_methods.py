"""Payment-rail classification + its drift guard.

``services/payment_methods`` decides which ``Payment.method`` rails the payer
reports on a 1099 and which the card settlement entity reports on a 1099-K.
Getting that wrong files a wrong number with the IRS in either direction, so
the registry has to stay exhaustive: the drift tests below fail the moment a
rail exists anywhere in the codebase without a tax treatment.
"""

from __future__ import annotations

import pytest

from app.schemas.payment import PaymentMethod
from app.services.payment_corridor import CORRIDOR_OVERRIDE_FEES
from app.services.payment_methods import (
    CARD_PAYMENT_METHODS,
    IRS_1099_REPORTABLE_METHODS,
    KNOWN_PAYMENT_METHODS,
    is_1099_reportable_method,
    is_card_payment_method,
    normalize_payment_method,
)

# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_card_and_reportable_sets_are_disjoint():
    assert CARD_PAYMENT_METHODS & IRS_1099_REPORTABLE_METHODS == frozenset()
    assert KNOWN_PAYMENT_METHODS == CARD_PAYMENT_METHODS | IRS_1099_REPORTABLE_METHODS


def test_virtual_card_is_the_card_rail():
    """The only rail settled over a payment card — the issuer/network files
    the 1099-K, so it must never reach our 1099 box amount."""
    assert is_card_payment_method("virtual_card") is True
    assert is_1099_reportable_method("virtual_card") is False


@pytest.mark.parametrize(
    "method",
    ["ach", "check", "wire", "rtp", "sepa", "international_ach", "international_wire"],
)
def test_bank_rails_are_1099_reportable(method):
    """ACH / cheque / wire / RTP / SEPA / IAT are all direct payments by the
    payer out of a bank account — squarely 1099-NEC/MISC reportable. Excluding
    any of them would UNDER-report, which is as wrong as over-reporting."""
    assert is_card_payment_method(method) is False
    assert is_1099_reportable_method(method) is True


@pytest.mark.parametrize("value", [None, "", "   ", "some_future_rail"])
def test_unknown_or_null_rail_stays_reportable(value):
    """NULL is what the manual / legacy payment paths write. Defaulting an
    unclassified rail OUT of the report would silently drop filed money."""
    assert is_card_payment_method(value) is False
    assert is_1099_reportable_method(value) is True


@pytest.mark.parametrize("value", ["VIRTUAL_CARD", " Virtual_Card ", "virtual_card "])
def test_classification_is_case_and_whitespace_insensitive(value):
    assert normalize_payment_method(value) == "virtual_card"
    assert is_card_payment_method(value) is True


# ---------------------------------------------------------------------------
# Drift guard — every rail the codebase can produce must be classified
# ---------------------------------------------------------------------------


def _unclassified(methods) -> set[str]:
    return {m for m in methods if normalize_payment_method(m) not in KNOWN_PAYMENT_METHODS}


def test_every_schema_enum_rail_is_classified():
    missing = _unclassified(m.value for m in PaymentMethod)
    assert not missing, (
        f"PaymentMethod rails with no 1099 treatment: {sorted(missing)}. "
        "Add each to CARD_PAYMENT_METHODS or IRS_1099_REPORTABLE_METHODS in "
        "app/services/payment_methods.py."
    )


def test_every_adapter_supported_rail_is_classified():
    # Importing the package self-registers every adapter.
    import app.services.payment_adapters  # noqa: F401, PLC0415
    from app.services.payment_adapters.dispatcher import _ADAPTER_REGISTRY  # noqa: PLC0415

    offered: set[str] = set()
    for adapter_cls in _ADAPTER_REGISTRY.values():
        offered.update(getattr(adapter_cls, "supported_methods", ()) or ())
    assert offered, "no payment adapters registered — the guard would pass vacuously"

    missing = _unclassified(offered)
    assert not missing, (
        f"payment-adapter rails with no 1099 treatment: {sorted(missing)}. "
        "Add each to CARD_PAYMENT_METHODS or IRS_1099_REPORTABLE_METHODS in "
        "app/services/payment_methods.py."
    )


def test_every_corridor_rail_is_classified():
    missing = _unclassified(CORRIDOR_OVERRIDE_FEES)
    assert not missing, (
        f"corridor rails with no 1099 treatment: {sorted(missing)}. "
        "Add each to CARD_PAYMENT_METHODS or IRS_1099_REPORTABLE_METHODS in "
        "app/services/payment_methods.py."
    )


def test_registry_carries_no_rail_the_codebase_cannot_produce():
    """The reverse direction — a stale entry here would be dead weight that
    quietly claims a treatment for a rail nobody writes any more."""
    import app.services.payment_adapters  # noqa: F401, PLC0415
    from app.services.payment_adapters.dispatcher import _ADAPTER_REGISTRY  # noqa: PLC0415

    producible = {m.value for m in PaymentMethod} | set(CORRIDOR_OVERRIDE_FEES)
    for adapter_cls in _ADAPTER_REGISTRY.values():
        producible.update(getattr(adapter_cls, "supported_methods", ()) or ())

    stale = KNOWN_PAYMENT_METHODS - {normalize_payment_method(m) for m in producible}
    assert not stale, f"classified rails nothing can produce: {sorted(stale)}"
