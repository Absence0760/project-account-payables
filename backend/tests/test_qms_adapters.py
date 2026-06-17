"""Unit tests for the QMS adapter family (pure — no DB, no network).

Covers the mock adapter's deterministic record set + override, the generic
adapter's fail-closed posture, and the dispatcher's registration + fallback.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.qms_adapters import QMSInspectionRecord, get_qms_adapter
from app.services.qms_adapters.generic_qms import GenericQMSAdapter
from app.services.qms_adapters.mock_adapter import MockQMSAdapter

# ---------------------------------------------------------------------------
# Mock adapter
# ---------------------------------------------------------------------------


async def test_mock_returns_fixed_pass_fail_partial_set():
    adapter = MockQMSAdapter()
    records = await adapter.fetch_inspections()

    assert len(records) == 3
    results = {r.result for r in records}
    assert results == {"pass", "fail", "partial"}
    # Quantities are Decimal, never float (money/quantity invariant).
    for r in records:
        if r.accepted_quantity is not None:
            assert isinstance(r.accepted_quantity, Decimal)
        if r.rejected_quantity is not None:
            assert isinstance(r.rejected_quantity, Decimal)


async def test_mock_is_deterministic_across_calls():
    adapter = MockQMSAdapter()
    first = await adapter.fetch_inspections()
    second = await adapter.fetch_inspections()
    assert [r.inspection_number for r in first] == [r.inspection_number for r in second]


async def test_mock_ignores_since_hint():
    from datetime import UTC, datetime

    adapter = MockQMSAdapter()
    records = await adapter.fetch_inspections(since=datetime.now(UTC))
    assert len(records) == 3


async def test_mock_records_override():
    adapter = MockQMSAdapter(
        {
            "mock_records": [
                {
                    "inspection_number": "X-1",
                    "result": "pass",
                    "po_number": "PO-9",
                    "accepted_quantity": "5.0",
                    "rejected_quantity": 0,
                }
            ]
        }
    )
    records = await adapter.fetch_inspections()
    assert len(records) == 1
    assert records[0].inspection_number == "X-1"
    assert records[0].accepted_quantity == Decimal("5.0")
    assert isinstance(records[0].accepted_quantity, Decimal)


async def test_mock_test_connection_true():
    assert await MockQMSAdapter().test_connection() is True


# ---------------------------------------------------------------------------
# Generic adapter — fails closed without credentials
# ---------------------------------------------------------------------------


async def test_generic_fetch_raises_without_credentials():
    adapter = GenericQMSAdapter({})
    with pytest.raises(RuntimeError, match="base_url.*api_key"):
        await adapter.fetch_inspections()


async def test_generic_fetch_raises_with_partial_credentials():
    # base_url but no api_key
    adapter = GenericQMSAdapter({"base_url": "https://qms.example.com"})
    with pytest.raises(RuntimeError):
        await adapter.fetch_inspections()


async def test_generic_test_connection_false_without_credentials():
    assert await GenericQMSAdapter({}).test_connection() is False


async def test_generic_test_connection_false_with_key_only():
    # Skeleton: even fully configured it returns False (no live impl).
    adapter = GenericQMSAdapter({"base_url": "https://x", "api_key": "k"})
    assert await adapter.test_connection() is False


# ---------------------------------------------------------------------------
# Dispatcher — registration + fallback
# ---------------------------------------------------------------------------


def test_dispatcher_defaults_to_mock_on_empty_config():
    assert get_qms_adapter(None).provider_name == "mock"
    assert get_qms_adapter({}).provider_name == "mock"


def test_dispatcher_unknown_provider_falls_back_to_mock():
    assert get_qms_adapter({"provider": "does-not-exist"}).provider_name == "mock"


def test_dispatcher_selects_generic():
    adapter = get_qms_adapter({"provider": "generic", "base_url": "https://x", "api_key": "k"})
    assert adapter.provider_name == "generic"
    assert isinstance(adapter, GenericQMSAdapter)


def test_dispatcher_provider_is_case_insensitive():
    assert get_qms_adapter({"provider": "MOCK"}).provider_name == "mock"


def test_record_dataclass_is_frozen():
    rec = QMSInspectionRecord(inspection_number="A", result="pass")
    with pytest.raises(Exception):
        rec.result = "fail"  # type: ignore[misc]
