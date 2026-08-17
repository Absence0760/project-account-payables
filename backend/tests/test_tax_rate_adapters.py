"""Tax-rate adapters — the probe must not outrun the contract.

The registry holds one working adapter (`mock`, which resolves rates from the
country-rules engine) and two skeletons (`avalara`, `taxjar`) whose `get_rate`
raises `NotImplementedError` no matter how it is configured. A skeleton that
answers `test_connection() is True` on credentials alone reports a healthy
connection for an integration that cannot satisfy its own core method — the
operator learns the truth on the first real rate lookup instead of at
configuration time.

The last test is the drift guard: it is written against the registry rather
than against the two known names, so a future skeleton adapter inherits the
rule automatically.
"""

from __future__ import annotations

import pytest

# Importing the adapter modules is what populates the registry (the dispatcher
# does the same imports lazily inside get_tax_rate_adapter).
import app.services.tax_rate_adapters.avalara  # noqa: F401
import app.services.tax_rate_adapters.mock_adapter  # noqa: F401
import app.services.tax_rate_adapters.taxjar  # noqa: F401
from app.services.tax_rate_adapters.dispatcher import _REGISTRY

# A config carrying every credential each adapter looks for, so "unconfigured"
# can never be the reason a probe fails below.
FULLY_CREDENTIALED = {
    "account_id": "acct-123",
    "api_key": "key-123",
    "base_url": "https://example.invalid",
}


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["avalara", "taxjar"])
async def test_skeleton_probe_is_false_even_fully_credentialed(provider):
    adapter = _REGISTRY[provider](dict(FULLY_CREDENTIALED))
    assert await adapter.test_connection() is False


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["avalara", "taxjar"])
async def test_skeleton_get_rate_still_refuses(provider):
    """The reason the probe must be False — the core method cannot answer."""
    adapter = _REGISTRY[provider](dict(FULLY_CREDENTIALED))
    with pytest.raises(NotImplementedError):
        await adapter.get_rate("GB")


@pytest.mark.asyncio
async def test_mock_adapter_is_a_real_working_provider():
    """The local-first default resolves a rate, so its True probe is earned."""
    adapter = _REGISTRY["mock"]({})
    assert await adapter.test_connection() is True
    result = await adapter.get_rate("GB")
    assert result.rate > 0


@pytest.mark.asyncio
async def test_no_adapter_reports_healthy_while_get_rate_raises():
    """Drift guard: registry-wide, not per-known-name.

    Any adapter whose `get_rate` raises `NotImplementedError` regardless of
    configuration is a skeleton, and a skeleton must report an unavailable
    connection.
    """
    for provider, cls in _REGISTRY.items():
        adapter = cls(dict(FULLY_CREDENTIALED))
        try:
            await adapter.get_rate("GB")
        except NotImplementedError:
            assert await adapter.test_connection() is False, (
                f"{provider} reports a healthy connection but its get_rate is unimplemented"
            )
        except Exception:
            # Any other failure is a runtime/config concern, not the
            # can-never-work condition this guard is about.
            continue
