"""Open Exchange Rates adapter — production FX rate provider.

OXR exposes a JSON endpoint that returns USD-anchored mid rates for
~170 currencies. The free tier covers USD as the base; paid tiers
allow other bases. We treat USD as the pivot regardless to keep the
contract identical between tiers.

API: https://docs.openexchangerates.org/reference/latest-json

Auth is via `?app_id=<key>` on every request. There is no rate-limit
header to honor — quota is enforced by the provider as 429s.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from app.services.fx_adapters.base import FXAdapter, FXRate
from app.services.fx_adapters.dispatcher import register_fx_adapter

logger = logging.getLogger(__name__)

_BASE_URL = "https://openexchangerates.org/api"


@register_fx_adapter("openexchangerates")
class OpenExchangeRatesAdapter:
    provider_name = "openexchangerates"

    def __init__(self, config: dict | None = None):
        cfg = config or {}
        self.app_id: str = cfg.get("api_key", "")
        self.timeout = float(cfg.get("timeout_seconds", 5.0))

    async def get_rate(self, source: str, target: str) -> FXRate:
        src = source.upper()
        tgt = target.upper()
        if src == tgt:
            return FXRate(
                source=src, target=tgt, rate=Decimal("1.0000"),
                as_of=datetime.now(UTC), provider=self.provider_name,
            )

        if not self.app_id:
            raise RuntimeError(
                "openexchangerates adapter requires `api_key` in fx config"
            )

        # OXR returns USD-base rates by default; we anchor on that and
        # compute the cross rate. `symbols=...` reduces payload size.
        symbols = ",".join({src, tgt})
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                f"{_BASE_URL}/latest.json",
                params={"app_id": self.app_id, "symbols": symbols, "prettyprint": "false"},
            )
            response.raise_for_status()
            body = response.json()

        rates: dict[str, float] = body.get("rates") or {}
        as_of_unix = body.get("timestamp")
        as_of = datetime.fromtimestamp(as_of_unix, tz=UTC) if as_of_unix else datetime.now(UTC)

        usd_to_src = _quantize(rates.get(src))
        usd_to_tgt = _quantize(rates.get(tgt))
        if usd_to_src is None or usd_to_tgt is None:
            raise RuntimeError(
                f"openexchangerates did not return rates for {src}/{tgt}"
            )

        rate = (usd_to_tgt / usd_to_src).quantize(Decimal("0.000001"))
        return FXRate(
            source=src, target=tgt, rate=rate, as_of=as_of,
            provider=self.provider_name,
        )

    async def test_connection(self) -> bool:
        try:
            # Fetching USD → EUR is the cheapest auth check.
            await self.get_rate("USD", "EUR")
        except Exception:  # noqa: BLE001
            return False
        return True


def _quantize(raw: float | int | None) -> Decimal | None:
    if raw is None:
        return None
    return Decimal(str(raw)).quantize(Decimal("0.00000001"))
