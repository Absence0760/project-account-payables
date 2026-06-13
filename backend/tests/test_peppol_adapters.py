"""Unit tests for the PEPPOL adapter family (no DB, no network).

Covers the ParticipantId value object (parse/format round-trip + PII-free
malformed errors), the mock adapter's resolve/send (success + failure paths),
the dispatcher registry + mock fallback, and the BIS Billing 3.0 constants.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from app.services.peppol_adapters import (
    PEPPOL_BIS_BILLING_DOCTYPE,
    PEPPOL_BIS_BILLING_PROCESSID,
    ParticipantId,
    TransmissionRequest,
    get_peppol_adapter,
    list_available_providers,
)
from app.services.peppol_adapters.as4_gateway import AS4GatewayAdapter
from app.services.peppol_adapters.base import _PEPPOL_ID_PREFIX
from app.services.peppol_adapters.mock_adapter import MockPeppolAdapter


class _FakeResponse:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code

    def json(self):
        return self._body


def _fake_async_client(responses, captured=None):
    calls = captured.setdefault("calls", []) if captured is not None else []
    response_iter = iter(responses)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            calls.append({"method": "POST", "url": url, **kw})
            return next(response_iter)

        async def get(self, url, **kw):
            calls.append({"method": "GET", "url": url, **kw})
            return next(response_iter)

    return _Client


# --------------------------------------------------------------------------
# ParticipantId
# --------------------------------------------------------------------------


def test_participant_id_parse_full_wire_form():
    pid = ParticipantId.parse("iso6523-actorid-upis::9930:DE123456789")
    assert pid.scheme == "9930"
    assert pid.value == "DE123456789"
    assert pid.format() == "iso6523-actorid-upis::9930:DE123456789"
    assert str(pid) == pid.format()


def test_participant_id_parse_bare_form():
    pid = ParticipantId.parse("9930:DE123456789")
    assert pid == ParticipantId(scheme="9930", value="DE123456789")


def test_participant_id_round_trip():
    original = "iso6523-actorid-upis::0088:7300010000001"
    assert ParticipantId.parse(original).format() == original


@pytest.mark.parametrize("bad", ["", "no-colon-here", "iso6523-actorid-upis::", "abc:DE123"])
def test_participant_id_parse_malformed_is_pii_free(bad):
    # The (possibly sensitive) value must NOT appear in the error message.
    with pytest.raises(ValueError) as excinfo:
        ParticipantId.parse(bad)
    msg = str(excinfo.value)
    assert msg.startswith("participant_id")
    # The value half of a malformed id never leaks.
    assert "DE123" not in msg
    assert "7300010000001" not in msg


def test_participant_id_prefix_constant():
    assert _PEPPOL_ID_PREFIX == "iso6523-actorid-upis"


# --------------------------------------------------------------------------
# Mock adapter
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mock_resolve_known_receiver_registered():
    adapter = MockPeppolAdapter({})
    cap = await adapter.resolve_participant(ParticipantId("9930", "DE123456789"))
    assert cap.registered is True
    assert cap.access_point_url
    assert PEPPOL_BIS_BILLING_DOCTYPE in cap.supported_doc_types


@pytest.mark.asyncio
async def test_mock_resolve_unknown_receiver_not_registered():
    adapter = MockPeppolAdapter({})
    cap = await adapter.resolve_participant(ParticipantId("9930", "UNREGISTERED-CO"))
    assert cap.registered is False
    assert cap.unregistered_reason == "receiver_not_registered"
    assert cap.access_point_url is None


@pytest.mark.asyncio
async def test_mock_send_success_returns_message_id():
    adapter = MockPeppolAdapter({})
    req = TransmissionRequest(
        sender=ParticipantId("9930", "DE000000000"),
        receiver=ParticipantId("9930", "DE123456789"),
        doc_type_id=PEPPOL_BIS_BILLING_DOCTYPE,
        process_id=PEPPOL_BIS_BILLING_PROCESSID,
        payload=b"<Invoice/>",
        business_message_id="abc123",
    )
    result = await adapter.send(req)
    assert result.success is True
    assert result.status == "sent"
    assert result.message_id
    # Deterministic per business_message_id (defence-in-depth idempotency key).
    assert (await adapter.send(req)).message_id == result.message_id


@pytest.mark.asyncio
async def test_mock_test_connection_true():
    assert await MockPeppolAdapter({}).test_connection() is True


# --------------------------------------------------------------------------
# Dispatcher / registry
# --------------------------------------------------------------------------


def test_registry_lists_mock_and_gateway():
    providers = list_available_providers()
    assert "mock" in providers
    assert "as4_gateway" in providers


def test_get_adapter_defaults_to_mock():
    assert get_peppol_adapter(None).provider_name == "mock"
    assert get_peppol_adapter({}).provider_name == "mock"


def test_get_adapter_unknown_provider_falls_back_to_mock():
    assert get_peppol_adapter({"provider": "does_not_exist"}).provider_name == "mock"


def test_get_adapter_selects_gateway():
    assert get_peppol_adapter({"provider": "as4_gateway"}).provider_name == "as4_gateway"


# --------------------------------------------------------------------------
# AS4 gateway adapter (HTTP fully stubbed — no real network)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_unconfigured_returns_pii_free_failure():
    """No API key (no hardcoded fallback) → 'peppol_not_configured', no network."""
    adapter = AS4GatewayAdapter({"gateway_url": "https://gw.example", "api_key": ""})
    cap = await adapter.resolve_participant(ParticipantId("9930", "DE123456789"))
    assert cap.registered is False
    assert cap.unregistered_reason == "peppol_not_configured"

    req = TransmissionRequest(
        sender=ParticipantId("9930", "DE000000000"),
        receiver=ParticipantId("9930", "DE123456789"),
        doc_type_id=PEPPOL_BIS_BILLING_DOCTYPE,
        process_id=PEPPOL_BIS_BILLING_PROCESSID,
        payload=b"<Invoice/>",
        business_message_id="bm-1",
    )
    result = await adapter.send(req)
    assert result.success is False
    assert result.failure_reason == "peppol_not_configured"
    assert await adapter.test_connection() is False


@pytest.mark.asyncio
async def test_gateway_api_key_from_settings_when_config_omits_it(monkeypatch):
    """The key with no config override comes from settings (sops in deployed)."""
    from app.config import settings

    monkeypatch.setattr(settings, "peppol_gateway_url", "https://gw.example")
    monkeypatch.setattr(settings, "peppol_gateway_api_key", "sops-supplied-key")
    adapter = AS4GatewayAdapter({"provider": "as4_gateway"})
    assert adapter.api_key == "sops-supplied-key"
    assert adapter.gateway_url == "https://gw.example"


@pytest.mark.asyncio
async def test_gateway_send_success_with_stubbed_http():
    captured: dict = {}
    fake = _fake_async_client(
        [_FakeResponse({"status": "sent", "message_id": "ap-msg-42"})], captured
    )
    adapter = AS4GatewayAdapter({"gateway_url": "https://gw.example", "api_key": "k"})
    req = TransmissionRequest(
        sender=ParticipantId("9930", "DE000000000"),
        receiver=ParticipantId("9930", "DE123456789"),
        doc_type_id=PEPPOL_BIS_BILLING_DOCTYPE,
        process_id=PEPPOL_BIS_BILLING_PROCESSID,
        payload=b"<Invoice/>",
        business_message_id="bm-xyz",
    )
    with patch("app.services.peppol_adapters.as4_gateway.httpx.AsyncClient", fake):
        result = await adapter.send(req)
    assert result.success is True
    assert result.message_id == "ap-msg-42"
    # business_message_id travels as the gateway idempotency key.
    assert captured["calls"][0]["headers"]["Idempotency-Key"] == "bm-xyz"
    # The receiver value (PII) is base64-wrapped, not in a header/log.
    assert captured["calls"][0]["json"]["receiver"] == "iso6523-actorid-upis::9930:DE123456789"


@pytest.mark.asyncio
async def test_gateway_send_transport_error_is_pii_free():
    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError("down")

    adapter = AS4GatewayAdapter({"gateway_url": "https://gw.example", "api_key": "k"})
    req = TransmissionRequest(
        sender=ParticipantId("9930", "DE000000000"),
        receiver=ParticipantId("9930", "DE123456789"),
        doc_type_id=PEPPOL_BIS_BILLING_DOCTYPE,
        process_id=PEPPOL_BIS_BILLING_PROCESSID,
        payload=b"<Invoice/>",
        business_message_id="bm-1",
    )
    with patch("app.services.peppol_adapters.as4_gateway.httpx.AsyncClient", _Boom):
        result = await adapter.send(req)
    assert result.success is False
    assert result.failure_reason == "gateway_transport_error:ConnectError"
    assert "DE123456789" not in (result.failure_reason or "")


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------


def test_bis_billing_constants_exact():
    assert PEPPOL_BIS_BILLING_DOCTYPE == (
        "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice"
        "##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"
    )
    assert PEPPOL_BIS_BILLING_PROCESSID == "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"
