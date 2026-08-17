"""Tests for TIN-validation adapters + offline format rules.

DB-free and network-free: exercises the deterministic ``format_rules`` and the
``mock`` / ``tax1099`` adapters directly. The IRS TIN-match transport path is
never pointed at a live endpoint — the offline-degrade behaviour (format-only
when no key, hard-fail on a malformed TIN) is asserted directly, and the
connection probe runs against an in-process ``httpx.MockTransport``.
"""

from __future__ import annotations

import httpx
import pytest

import app.services.tin_validation_adapters.tax1099_adapter as tax1099_tin
from app.services.tin_validation_adapters import get_tin_validation_adapter
from app.services.tin_validation_adapters.base import (
    VERDICT_INVALID,
    VERDICT_VALID,
)
from app.services.tin_validation_adapters.format_rules import check_format, normalize_digits

# ---------------------------------------------------------------------------
# Offline format rules
# ---------------------------------------------------------------------------


def test_normalize_strips_separators():
    assert normalize_digits("12-3456789") == "123456789"
    assert normalize_digits("123 45 6789") == "123456789"


def test_valid_ein_format():
    fc = check_format("12-3456789", "ein")
    assert fc.ok is True
    assert fc.tin_type == "ein"
    assert fc.last4 == "6789"


def test_valid_ssn_format():
    fc = check_format("123-45-6789", "ssn")
    assert fc.ok is True
    assert fc.tin_type == "ssn"


@pytest.mark.parametrize("bad", ["1234", "12-345678", "abcdefghi", ""])
def test_wrong_length_is_invalid(bad):
    fc = check_format(bad)
    assert fc.ok is False
    assert fc.reason_code == "format_invalid"
    # No digits leak: last4 is None when unparseable.
    assert fc.last4 is None


def test_ein_invalid_prefix_rejected():
    # "00" is a never-issued EIN prefix.
    fc = check_format("00-1234567", "ein")
    assert fc.ok is False
    assert fc.reason_code == "ein_invalid_prefix"


def test_ssn_invalid_area_rejected():
    fc = check_format("000-12-3456", "ssn")
    assert fc.ok is False
    assert fc.reason_code == "ssn_invalid_area"


def test_ssn_invalid_group_rejected():
    fc = check_format("123-00-4567", "ssn")
    assert fc.ok is False
    assert fc.reason_code == "ssn_invalid_group"


def test_type_inferred_from_separator_shape():
    assert check_format("12-3456789").tin_type == "ein"
    assert check_format("123-45-6789").tin_type == "ssn"


# ---------------------------------------------------------------------------
# Mock adapter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_adapter_accepts_valid_ein():
    adapter = get_tin_validation_adapter({"provider": "mock"})
    result = await adapter.validate(tin="12-3456789", legal_name="Acme LLC")
    assert result.verdict == VERDICT_VALID
    assert result.tin_last4 == "6789"
    # Offline adapter can't reach the IRS → name_match unchecked.
    assert result.name_match is None


@pytest.mark.asyncio
async def test_mock_adapter_rejects_malformed():
    adapter = get_tin_validation_adapter(None)  # defaults to mock
    result = await adapter.validate(tin="00-0000000")
    assert result.verdict == VERDICT_INVALID
    assert result.is_valid is False


@pytest.mark.asyncio
async def test_mock_adapter_result_carries_no_raw_tin():
    adapter = get_tin_validation_adapter({"provider": "mock"})
    result = await adapter.validate(tin="12-3456789")
    serialized = result.to_dict()
    assert "123456789" not in str(serialized)
    assert serialized["tin_last4"] == "6789"


@pytest.mark.asyncio
async def test_mock_adapter_simulated_name_mismatch():
    adapter = get_tin_validation_adapter({"provider": "mock", "mock_name_mismatch_last4": ["6789"]})
    result = await adapter.validate(tin="12-3456789", legal_name="Wrong Name Inc")
    assert result.verdict == VERDICT_INVALID
    assert result.name_match is False
    assert result.reason_code == "irs_mismatch"


# ---------------------------------------------------------------------------
# Tax1099 skeleton — offline-degrade behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tax1099_without_key_degrades_to_format_only():
    adapter = get_tin_validation_adapter({"provider": "tax1099"})  # no api_key
    result = await adapter.validate(tin="12-3456789", legal_name="Acme LLC")
    assert result.verdict == VERDICT_VALID
    assert result.reason_code == "format_only_no_api_key"
    assert result.name_match is None


@pytest.mark.asyncio
async def test_tax1099_hard_fails_malformed_without_calling_out():
    adapter = get_tin_validation_adapter({"provider": "tax1099", "api_key": "live-key"})
    # Malformed TIN never reaches the network — caught by the offline check.
    result = await adapter.validate(tin="not-a-tin")
    assert result.verdict == VERDICT_INVALID
    assert result.reason_code == "format_invalid"


# ---------------------------------------------------------------------------
# Tax1099 skeleton — the probe must actually probe
# ---------------------------------------------------------------------------
#
# `test_connection` used to call `validate(tin="00-0000000")`, on the theory
# that a malformed TIN exercised auth without spending a real lookup. It did
# not: `validate` runs the OFFLINE `check_format` first and returns
# `format_invalid` before any HTTP, so the probe never left the process and
# answered True for ANY non-empty `api_key`. A connection test that reports
# healthy on the mere presence of a credential is worse than none — it is the
# surface an operator uses to catch a wrong key, and it confirmed the mistake.


def _mock_httpx(monkeypatch, handler):
    """Point the adapter's `httpx.AsyncClient` at an in-process transport."""
    real_client = httpx.AsyncClient

    def _factory(**kwargs):
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(tax1099_tin.httpx, "AsyncClient", _factory)


@pytest.mark.asyncio
async def test_tax1099_probe_reaches_the_provider(monkeypatch):
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"status": "ok"})

    _mock_httpx(monkeypatch, handler)
    adapter = get_tin_validation_adapter({"provider": "tax1099", "api_key": "live-key"})

    assert await adapter.test_connection() is True
    assert seen["url"].endswith("/account/ping")
    assert seen["auth"] == "Bearer live-key"


@pytest.mark.asyncio
async def test_tax1099_probe_is_false_on_a_rejected_credential(monkeypatch):
    """The regression. A 401 from the provider is exactly the case the probe
    exists to surface, and the old implementation answered True."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    _mock_httpx(monkeypatch, handler)
    adapter = get_tin_validation_adapter({"provider": "tax1099", "api_key": "wrong-key"})

    assert await adapter.test_connection() is False


@pytest.mark.asyncio
async def test_tax1099_probe_is_false_when_the_provider_is_unreachable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    _mock_httpx(monkeypatch, handler)
    adapter = get_tin_validation_adapter({"provider": "tax1099", "api_key": "live-key"})

    assert await adapter.test_connection() is False


@pytest.mark.asyncio
async def test_tax1099_probe_is_false_without_a_key_and_makes_no_call(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        raise AssertionError("unconfigured probe must not call out")

    _mock_httpx(monkeypatch, handler)
    adapter = get_tin_validation_adapter({"provider": "tax1099"})

    assert await adapter.test_connection() is False
