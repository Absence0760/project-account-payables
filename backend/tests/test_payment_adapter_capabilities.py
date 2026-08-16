"""Drift guard: every OPTIONAL payment-adapter capability is classified.

``PaymentAdapter`` (``services/payment_adapters/base.py``) declares four methods
a concrete adapter MAY override — ``quote_payment``, ``get_balance``,
``fetch_settlement`` and ``void_payment``. Each has a base implementation that
reports "not available" so a caller degrades cleanly instead of crashing.

That softness is the hazard this file exists for. Registering a new processor
that silently inherits all four is invisible: the corridor comparison skips it,
the cash-position curve falls back to the manual opening balance, its
settlements verify as ``unverified`` forever, and ``/void`` books a
bookkeeping-only void while the money is still in flight at the processor. No
test fails, because in every case the *inherited* code works.

So this file requires an explicit answer per capability. A newly registered
adapter must appear in ``IMPLEMENTS`` or in ``DOES_NOT_IMPLEMENT`` — with the
consequence of not implementing it written down — before the suite goes green.
Same shape as ``tests/test_payment_methods.py``, which guards the *rails* an
adapter offers; this one guards its *capabilities*.

It also pins the one behaviour that made the softness dangerous rather than
merely lossy: ``quote_payment``'s base default must FAIL CLOSED. It used to
return a fabricated ``available=True`` zero-fee, zero-ETA quote, which beat
every sibling publishing a real fee on both ``cheapest`` and ``fastest`` ranking
modes — routing money on numbers nobody supplied. See
``docs/decisions.md`` §22's neighbourhood and the base-class docstring.

Pure Python: no DB, no network. Importing the adapter package self-registers
every adapter, exactly as production does.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

import app.services.payment_adapters  # noqa: F401  (self-registers every adapter)
from app.services.payment_adapters.base import PaymentAdapter, PaymentPayload
from app.services.payment_adapters.dispatcher import _ADAPTER_REGISTRY

#: The optional capabilities. Every registered adapter must be classified for
#: each of these, in exactly one of the two maps below.
OPTIONAL_CAPABILITIES = (
    "quote_payment",
    "get_balance",
    "fetch_settlement",
    "void_payment",
)

#: capability → adapters that override the base implementation.
IMPLEMENTS: dict[str, frozenset[str]] = {
    "quote_payment": frozenset(
        {"mock", "column", "increase", "dwolla", "checkeeper", "stripe_treasury"}
    ),
    "get_balance": frozenset({"mock"}),
    "fetch_settlement": frozenset({"mock", "dwolla"}),
    "void_payment": frozenset({"mock"}),
}

#: capability → adapters that deliberately inherit the base "not available"
#: implementation, and what the caller therefore sees. Moving an adapter out of
#: here is the point: each line is a known gap, not an accident.
DOES_NOT_IMPLEMENT: dict[str, dict[str, str]] = {
    "quote_payment": {
        # Modern Treasury publishes no quote endpoint and we hold no static fee
        # table for it, so it is skipped by the corridor comparison rather than
        # winning it with a fabricated free/instant quote (what the old
        # permissive base default did). Add its real schedule to un-skip it.
        "modern_treasury": "skipped by compare_quotes (no_quote_endpoint)",
    },
    "get_balance": {
        "modern_treasury": "cash-position opening balance falls back to the manual figure",
        "stripe_treasury": "cash-position opening balance falls back to the manual figure",
        "increase": "cash-position opening balance falls back to the manual figure",
        "column": "cash-position opening balance falls back to the manual figure",
        "dwolla": "cash-position opening balance falls back to the manual figure",
        "checkeeper": "check printing has no funding account to report",
    },
    "fetch_settlement": {
        "modern_treasury": "webhook carries the amount; reconciler-settled rows stay unverified",
        "stripe_treasury": "webhook carries the amount; reconciler-settled rows stay unverified",
        "increase": "webhook carries the amount; reconciler-settled rows stay unverified",
        "column": "webhook carries the amount; reconciler-settled rows stay unverified",
        "checkeeper": "a printed cheque has no settlement figure to pull",
    },
    "void_payment": {
        "modern_treasury": "void is local bookkeeping only — the processor is not asked",
        "stripe_treasury": "void is local bookkeeping only — the processor is not asked",
        "increase": "void is local bookkeeping only — the processor is not asked",
        "column": "void is local bookkeeping only — the processor is not asked",
        "dwolla": "void is local bookkeeping only — the processor is not asked",
        "checkeeper": "void is local bookkeeping only — the processor is not asked",
    },
}


def _overrides(adapter_cls: type, capability: str) -> bool:
    """True when ``adapter_cls`` defines its own ``capability``, not the base's."""
    return getattr(adapter_cls, capability) is not getattr(PaymentAdapter, capability)


def test_registry_is_populated():
    """A guard over an empty registry would pass forever."""
    assert len(_ADAPTER_REGISTRY) >= 5, sorted(_ADAPTER_REGISTRY)


@pytest.mark.parametrize("capability", OPTIONAL_CAPABILITIES)
def test_every_adapter_is_classified_for_each_capability(capability):
    implements = IMPLEMENTS[capability]
    does_not = set(DOES_NOT_IMPLEMENT[capability])
    unclassified = sorted(set(_ADAPTER_REGISTRY) - implements - does_not)
    assert not unclassified, (
        f"payment adapters with no recorded answer for '{capability}': {unclassified}. "
        f"Implement it, or add it to DOES_NOT_IMPLEMENT['{capability}'] in "
        "tests/test_payment_adapter_capabilities.py with what the caller then sees."
    )


@pytest.mark.parametrize("capability", OPTIONAL_CAPABILITIES)
def test_the_classification_matches_the_code(capability):
    """Both directions — a stale entry is as bad as a missing one."""
    for name, adapter_cls in sorted(_ADAPTER_REGISTRY.items()):
        overrides = _overrides(adapter_cls, capability)
        if name in IMPLEMENTS[capability]:
            assert overrides, (
                f"'{name}' is listed as implementing '{capability}' but inherits the base."
            )
        elif name in DOES_NOT_IMPLEMENT[capability]:
            assert not overrides, (
                f"'{name}' now implements '{capability}' — move it into "
                f"IMPLEMENTS['{capability}'] (and drop the recorded consequence)."
            )


def _payload(method: str = "ach") -> PaymentPayload:
    return PaymentPayload(
        correlation_id="cor-capability-1",
        invoice_id="inv-1",
        invoice_number="INV-1",
        vendor_name="Vendor Co",
        amount=Decimal("1000.00"),
        currency="USD",
        method=method,
    )


class _BareAdapter(PaymentAdapter):
    """An adapter that overrides nothing optional — the inherited-defaults case."""

    provider_name = "bare"
    supported_methods = ("ach",)

    async def create_payment(self, payload):  # pragma: no cover - not exercised
        raise NotImplementedError

    async def get_payment_status(self, provider_payment_id):  # pragma: no cover
        raise NotImplementedError

    def parse_webhook(self, headers, body):  # pragma: no cover
        return None

    async def test_connection(self):  # pragma: no cover
        return False


async def test_base_quote_payment_fails_closed_on_a_supported_method():
    """The load-bearing one. A permissive zero-fee default beats every sibling
    that publishes a real fee, on both ranking modes, with a number nobody
    supplied — so an adapter with no fee schedule must be SKIPPED, not chosen."""
    quote = await _BareAdapter({}).quote_payment(_payload(method="ach"))
    assert quote.available is False
    assert quote.unavailable_reason == "no_quote_endpoint"
    # Unavailable quotes cost Infinity, so ranking can never pick this one.
    assert quote.total_cost(Decimal("1000.00")) == Decimal("Infinity")


async def test_base_quote_payment_still_names_an_unsupported_method():
    """The unsupported-rail reason is more specific than the missing-schedule
    one, and stays that way — it tells the operator a different thing."""
    quote = await _BareAdapter({}).quote_payment(_payload(method="wire"))
    assert quote.available is False
    assert "wire" in (quote.unavailable_reason or "")
