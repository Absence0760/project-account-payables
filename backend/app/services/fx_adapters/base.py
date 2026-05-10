"""FX adapter interface."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True)
class FXRate:
    """A mid-market FX rate at a point in time.

    `rate` is the multiplier you apply to one unit of `source` to get
    units of `target`. e.g. EUR → USD rate 1.08 means 1 EUR = 1.08 USD.

    `as_of` is the timestamp the rate was observed (provider-supplied
    when available; UTC now() otherwise). We persist this on the
    Payment row so an audit can replay the price the customer saw.
    """

    source: str
    target: str
    rate: Decimal
    as_of: datetime
    provider: str


class FXAdapter(Protocol):
    """The minimum contract every FX provider must satisfy."""

    provider_name: str

    async def get_rate(self, source: str, target: str) -> FXRate:
        """Look up the mid-market rate `source` → `target`.

        Caller passes uppercase ISO 4217 codes (e.g. "USD", "EUR").
        Implementations must raise on unknown currencies — silently
        returning a stale or zero rate would mis-price every payment
        in that corridor.
        """
        ...

    async def test_connection(self) -> bool:
        """Probe the provider with the cheapest available call (auth
        check, single rate fetch, etc.). Returns True on success."""
        ...
