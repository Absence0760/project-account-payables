"""FX adapter contract — mock + dispatcher.

The mock adapter is what every test (and local dev) uses, so its
behavior matters. Pins:
  - same-currency request always returns rate 1.0 (no provider call
    needed)
  - cross rates compute via USD pivot
  - `mock_rates` config injection wins over defaults (lets tests
    simulate market moves between t0 and t1)
  - unknown currencies raise — silently returning a stale rate
    would mis-price every payment in that corridor
  - dispatcher falls back to mock on missing config / unknown
    provider (sensible for local; obviously wrong in prod, but
    deployments set `provider` explicitly)

OpenExchangeRates is a thin HTTP wrapper. The unit test stubs httpx
so we never touch the live API — we just pin the request shape
(auth via `app_id` query string) and the response parsing (USD-base
rates folded into the requested pair).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.fx_adapters import FXRate, get_fx_adapter
from app.services.fx_adapters.mock_adapter import MockFXAdapter, _UnknownCurrency
from app.services.fx_adapters.openexchangerates import OpenExchangeRatesAdapter

# ---------------------------------------------------------------------------
# MockFXAdapter — the workhorse of every test below.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_same_currency_returns_unit_rate():
    """USD → USD is always 1.0 regardless of any other config. A
    regression that ran the cross-rate math here would emit weird
    near-1.0 floats from rounding."""
    adapter = MockFXAdapter()
    rate = await adapter.get_rate("USD", "USD")
    assert rate.rate == Decimal("1.0000")
    assert rate.source == "USD"
    assert rate.target == "USD"
    assert rate.provider == "mock"


@pytest.mark.asyncio
async def test_mock_known_pair_returns_published_rate():
    """USD → EUR uses the published table directly."""
    adapter = MockFXAdapter()
    rate = await adapter.get_rate("USD", "EUR")
    assert rate.rate == Decimal("0.92")


@pytest.mark.asyncio
async def test_mock_cross_rate_computes_via_usd_pivot():
    """EUR → GBP = rate(USD → GBP) / rate(USD → EUR) = 0.79 / 0.92."""
    adapter = MockFXAdapter()
    rate = await adapter.get_rate("EUR", "GBP")
    expected = (Decimal("0.79") / Decimal("0.92")).quantize(Decimal("0.000001"))
    assert rate.rate == expected


@pytest.mark.asyncio
async def test_mock_config_overrides_take_precedence():
    """Caller injects `mock_rates` → those override the defaults.
    This is how tests simulate market moves between invoice booking
    and payment settlement."""
    adapter = MockFXAdapter({"mock_rates": {"EUR": "0.80"}})  # EUR weaker
    rate = await adapter.get_rate("USD", "EUR")
    assert rate.rate == Decimal("0.80")


@pytest.mark.asyncio
async def test_mock_overrides_accept_decimal_string_or_float():
    """Test harnesses pass strings; old code might pass floats.
    Coerce safely without losing precision."""
    adapter_str = MockFXAdapter({"mock_rates": {"EUR": "0.8500"}})
    adapter_float = MockFXAdapter({"mock_rates": {"EUR": 0.85}})
    rate1 = await adapter_str.get_rate("USD", "EUR")
    rate2 = await adapter_float.get_rate("USD", "EUR")
    assert rate1.rate == Decimal("0.8500")
    assert rate2.rate == Decimal("0.85")


@pytest.mark.asyncio
async def test_mock_unknown_currency_raises():
    """An unknown source or target raises — silently returning a
    stale or zero rate would mis-price every payment in that
    corridor."""
    adapter = MockFXAdapter()
    with pytest.raises(_UnknownCurrency):
        await adapter.get_rate("USD", "XYZ")
    with pytest.raises(_UnknownCurrency):
        await adapter.get_rate("XYZ", "USD")


@pytest.mark.asyncio
async def test_mock_test_connection_is_always_true():
    adapter = MockFXAdapter()
    assert await adapter.test_connection() is True


# ---------------------------------------------------------------------------
# Dispatcher.
# ---------------------------------------------------------------------------


def test_dispatcher_falls_back_to_mock_when_config_is_empty():
    """Empty config → mock. Documented behavior for local dev."""
    adapter = get_fx_adapter(None)
    assert adapter.provider_name == "mock"
    adapter = get_fx_adapter({})
    assert adapter.provider_name == "mock"


def test_dispatcher_falls_back_to_mock_on_unknown_provider():
    """A typo in `fx.provider` shouldn't take the org's payments
    offline. Falls back to mock; in prod, this just means tests pass
    locally without surprising config."""
    adapter = get_fx_adapter({"provider": "made_up_provider"})
    assert adapter.provider_name == "mock"


def test_dispatcher_routes_to_openexchangerates_when_configured():
    adapter = get_fx_adapter({"provider": "openexchangerates", "api_key": "k"})
    assert adapter.provider_name == "openexchangerates"


# ---------------------------------------------------------------------------
# OpenExchangeRatesAdapter — HTTP wiring.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=MagicMock(), response=MagicMock())


@pytest.mark.asyncio
async def test_openexchangerates_builds_request_with_app_id_and_symbols():
    """The adapter must include `app_id` (auth) and `symbols`
    (payload reduction) on every request. A regression that dropped
    `app_id` would 401 every call."""
    captured_params: dict = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            captured_params.update(params or {})
            return _FakeResponse(
                {
                    "timestamp": 1715000000,
                    "rates": {"USD": 1.0, "EUR": 0.92},
                }
            )

    adapter = OpenExchangeRatesAdapter({"api_key": "secret-123"})
    with patch("app.services.fx_adapters.openexchangerates.httpx.AsyncClient", _FakeClient):
        rate = await adapter.get_rate("USD", "EUR")

    assert captured_params["app_id"] == "secret-123"
    # Symbols param contains both codes (order-independent).
    assert "USD" in captured_params["symbols"]
    assert "EUR" in captured_params["symbols"]
    assert rate.rate == Decimal("0.920000")


@pytest.mark.asyncio
async def test_openexchangerates_raises_without_api_key():
    """No api_key in config → RuntimeError. Calling the API without
    auth would 401 anyway, but we surface a friendlier error so the
    operator knows to set the key."""
    adapter = OpenExchangeRatesAdapter({})
    with pytest.raises(RuntimeError, match="api_key"):
        await adapter.get_rate("USD", "EUR")


@pytest.mark.asyncio
async def test_openexchangerates_cross_rate_uses_usd_pivot():
    """GBP → EUR via USD pivot: rate(USD → EUR) / rate(USD → GBP).
    OXR returns both rates relative to USD in the same response."""

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _FakeResponse(
                {
                    "timestamp": 1715000000,
                    "rates": {"GBP": 0.79, "EUR": 0.92},
                }
            )

    adapter = OpenExchangeRatesAdapter({"api_key": "k"})
    with patch("app.services.fx_adapters.openexchangerates.httpx.AsyncClient", _FakeClient):
        rate = await adapter.get_rate("GBP", "EUR")

    expected = (Decimal("0.92") / Decimal("0.79")).quantize(Decimal("0.000001"))
    assert rate.rate == expected


@pytest.mark.asyncio
async def test_openexchangerates_missing_rate_raises():
    """Provider returned a body but the symbol we asked for isn't
    in it — defensive against a future provider regression."""

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _FakeResponse({"rates": {}, "timestamp": 1715000000})

    adapter = OpenExchangeRatesAdapter({"api_key": "k"})
    with patch("app.services.fx_adapters.openexchangerates.httpx.AsyncClient", _FakeClient):
        with pytest.raises(RuntimeError, match="did not return rates"):
            await adapter.get_rate("USD", "EUR")


def test_fxrate_is_immutable():
    """FXRate is frozen — once locked on a Payment row, nothing can
    rewrite it. A regression to a mutable dataclass would let a
    later code path overwrite the locked rate during settlement,
    breaking the audit replay."""
    from datetime import UTC, datetime

    rate = FXRate(
        source="USD", target="EUR", rate=Decimal("0.9"), as_of=datetime.now(UTC), provider="mock"
    )
    with pytest.raises(Exception):  # FrozenInstanceError
        rate.rate = Decimal("1.0")  # type: ignore[misc]
