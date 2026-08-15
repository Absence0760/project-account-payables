"""Unit coverage for the ``extract_statement`` extraction-adapter capability.

Covers the optional-capability default (an adapter that hasn't implemented it
reports "not supported" instead of pretending), the shared model-payload
parser, the deterministic offline reader the ``mock`` adapter uses in place of a
model, and both model-backed adapters' happy + failure paths with the network
stubbed.

See ``backend/docs/vendor-statement-reconciliation.md`` § PDF intake.
"""

from __future__ import annotations

import json

import pytest

from app.services.extraction_adapters.base import (
    STATEMENT_REASON_EMPTY_FILE,
    STATEMENT_REASON_NO_LINES,
    STATEMENT_REASON_NO_TEXT_LAYER,
    STATEMENT_REASON_NOT_SUPPORTED,
    STATEMENT_REASON_PROVIDER_ERROR,
    STATEMENT_REASON_UNREADABLE,
    ExtractionAdapter,
    pdf_text_layer,
)
from app.services.extraction_adapters.claude_vision import ClaudeVisionAdapter
from app.services.extraction_adapters.mock_adapter import MockExtractionAdapter
from app.services.extraction_adapters.ollama import OllamaAdapter
from app.services.extraction_adapters.statement_extraction import (
    parse_statement_payload,
    scan_statement_text,
)

_STATEMENT_TEXT = """\
Globex Industrial
Statement of Account
Statement date: 2026-02-28

Invoice     Date          Amount
INV-1001    2026-01-15    1,200.00
INV-1002    01/20/2026    $850.50
INV-1003    2026-02-01    (250.00)

Total                     1,800.50
Page 1 of 1
"""


def _pdf_with_text(text: str) -> bytes:
    """Render a real PDF carrying a text layer, so the reader is exercised
    through PyMuPDF rather than a stub."""
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 60), text, fontsize=9, fontname="cour")
    return doc.tobytes()


# --------------------------------------------------------------------------- #
# Optional-capability default
# --------------------------------------------------------------------------- #


async def test_base_adapter_reports_statement_extraction_unavailable():
    """The default must say "I can't", never return an empty success — an empty
    success would create a reconciliation run claiming the supplier listed
    nothing."""
    result = await ExtractionAdapter({}).extract_statement(b"%PDF-1.4 whatever")
    assert result.available is False
    assert result.success is False
    assert result.reason == STATEMENT_REASON_NOT_SUPPORTED
    assert result.lines == []


async def test_unimplemented_registered_adapter_inherits_the_default():
    """aws_textract / openai_vision haven't implemented the capability; they
    must inherit the honest default rather than silently mis-reading."""
    from app.services.extraction_adapters.openai_vision import OpenAIVisionAdapter

    result = await OpenAIVisionAdapter({}).extract_statement(b"%PDF-1.4")
    assert result.available is False
    assert result.reason == STATEMENT_REASON_NOT_SUPPORTED


# --------------------------------------------------------------------------- #
# Shared model-payload parser
# --------------------------------------------------------------------------- #


def test_parse_statement_payload_reads_wrapped_and_bare_fields():
    result = parse_statement_payload(
        {
            "lines": [
                {
                    "invoice_number": {"value": "INV-1", "confidence": 0.9},
                    "invoice_date": {"value": "2026-01-15", "confidence": 0.8},
                    "amount": {"value": "1200.00", "confidence": 0.95},
                    "status": {"value": "open", "confidence": 0.4},
                },
                # Bare scalars — some models drop the {value, confidence} wrapper.
                {"invoice_number": "INV-2", "amount": "-250.00"},
            ]
        },
        "claude_vision",
    )
    assert result.available is True
    assert result.success is True
    assert [ln.invoice_number for ln in result.lines] == ["INV-1", "INV-2"]
    assert result.lines[0].amount == "1200.00"
    assert result.lines[0].invoice_date == "2026-01-15"
    # Credits survive as a signed string — the service parses it into Decimal.
    assert result.lines[1].amount == "-250.00"
    assert 0 < result.overall_confidence <= 1


def test_parse_statement_payload_drops_rows_with_nothing_to_match_on():
    result = parse_statement_payload(
        {
            "lines": [
                {"invoice_number": {"value": None}, "amount": {"value": None}},
                {"invoice_number": {"value": "INV-9", "confidence": 0.9}},
                "not-a-dict",
            ]
        },
        "mock",
    )
    assert [ln.invoice_number for ln in result.lines] == ["INV-9"]


@pytest.mark.parametrize(
    "payload,expected",
    [
        ({"lines": []}, STATEMENT_REASON_NO_LINES),
        ({"lines": [{"invoice_number": {"value": None}}]}, STATEMENT_REASON_NO_LINES),
        ({"nope": 1}, STATEMENT_REASON_UNREADABLE),
        ([], STATEMENT_REASON_UNREADABLE),
        ("garbage", STATEMENT_REASON_UNREADABLE),
    ],
)
def test_parse_statement_payload_failure_reasons(payload, expected):
    result = parse_statement_payload(payload, "mock")
    assert result.success is False
    assert result.reason == expected
    # `available` stays True — the provider CAN do this, it just didn't work.
    assert result.available is True


def test_parse_statement_payload_never_emits_a_float():
    """Money leaves an adapter as a string; a float here would be a rounding
    bug the reconciliation engine could never undo."""
    result = parse_statement_payload(
        {"lines": [{"invoice_number": "INV-1", "amount": {"value": 1200.10}}]}, "mock"
    )
    assert result.lines[0].amount == "1200.1"
    assert isinstance(result.lines[0].amount, str)


# --------------------------------------------------------------------------- #
# Deterministic offline reader
# --------------------------------------------------------------------------- #


def test_scan_statement_text_reads_rows_and_skips_furniture():
    lines = scan_statement_text(_STATEMENT_TEXT)
    assert [ln.invoice_number for ln in lines] == ["INV-1001", "INV-1002", "INV-1003"]
    assert [ln.amount for ln in lines] == ["1,200.00", "$850.50", "(250.00)"]
    assert [ln.invoice_date for ln in lines] == ["2026-01-15", "01/20/2026", "2026-02-01"]
    # Header row, the total, the page footer and the address block are all gone.
    # A heuristic read must not claim a model's clearly-printed confidence.
    assert all(ln.confidence < 0.8 for ln in lines)


@pytest.mark.parametrize(
    "row",
    [
        "Invoice     Date          Amount",
        "Total                     1,800.50",
        "Balance forward           500.00",
        "Page 1 of 2",
        "Globex Industrial",
        "Statement date: 2026-02-28",
        "",
    ],
)
def test_scan_statement_text_rejects_non_item_rows(row):
    assert scan_statement_text(row) == []


def test_scan_statement_text_handles_a_leading_row_counter():
    lines = scan_statement_text("1  INV-1002  01/20/2026  850.50")
    assert len(lines) == 1
    assert lines[0].invoice_number == "INV-1002"
    assert lines[0].amount == "850.50"


def test_scan_statement_text_handles_a_row_with_no_date_column():
    lines = scan_statement_text("INV-7001   4200.00")
    assert len(lines) == 1
    assert lines[0].invoice_number == "INV-7001"
    assert lines[0].invoice_date is None


@pytest.mark.parametrize(
    "row",
    [
        # A payment-terms column between the date and the balance.
        "INV-1001    2026-01-15    Net 30    1,200.00",
        # An aging-days column in the same place.
        "INV-1001    2026-01-15    45    1,200.00",
    ],
)
def test_scan_statement_text_does_not_read_a_bare_integer_column_as_the_balance(row):
    """`Net 30` / `45 days` are amount-SHAPED but are not money. Reading one as
    the open balance is silently wrong money — the one outcome this reader must
    never produce."""
    lines = scan_statement_text(row)
    assert len(lines) == 1
    assert lines[0].amount == "1,200.00"


def test_scan_statement_text_accepts_a_lone_whole_number_balance():
    """A statement that prints no cents still reconciles when the row is
    unambiguous."""
    lines = scan_statement_text("INV-7001   2026-01-15   4200")
    assert [ln.amount for ln in lines] == ["4200"]


@pytest.mark.parametrize(
    "row",
    [
        # invoice-amount + balance-due: nothing on the row says which is open.
        "INV-1001   2026-01-15   1,200.00   950.00",
        # balance + a trailing aging bucket.
        "INV-1001   2026-01-15   1,200.00   0.00",
        # No cents anywhere and two unlabelled integer columns.
        "INV-7001   2026-01-15   45   4200",
    ],
)
def test_scan_statement_text_skips_a_row_with_more_than_one_money_column(row):
    """Two money columns is a guess, and a guessed open balance is wrong money
    presented as fact. Skipping leaves our invoice visible as
    `missing_on_their_side` — a difference the clerk chases."""
    assert scan_statement_text(row) == []


# --------------------------------------------------------------------------- #
# mock adapter — the offline, credential-free path
# --------------------------------------------------------------------------- #


async def test_mock_reads_a_real_pdf_text_layer():
    pdf = _pdf_with_text(_STATEMENT_TEXT)
    result = await MockExtractionAdapter({}).extract_statement(pdf, "s.pdf", "application/pdf")
    assert result.available is True
    assert result.success is True
    assert [ln.invoice_number for ln in result.lines] == ["INV-1001", "INV-1002", "INV-1003"]
    assert result.provider == "mock"


async def test_mock_reads_plain_text_payloads_too():
    result = await MockExtractionAdapter({}).extract_statement(
        _STATEMENT_TEXT.encode(), "s.txt", "text/plain"
    )
    assert result.success is True
    assert len(result.lines) == 3


async def test_mock_gives_up_loudly_on_a_scan_instead_of_inventing_lines():
    """A scanned statement has no text layer. The mock must NOT fall back to a
    fixture — a fabricated open item is money a clerk would chase."""
    scanned = _pdf_with_text("")  # a PDF with no meaningful text
    result = await MockExtractionAdapter({}).extract_statement(scanned, "scan.pdf")
    assert result.available is True
    assert result.success is False
    assert result.reason == STATEMENT_REASON_NO_TEXT_LAYER
    assert result.lines == []


async def test_mock_reports_a_readable_document_that_has_no_open_items():
    text = b"Dear customer,\n\nThank you for your business.\n\nRegards,\nGlobex"
    result = await MockExtractionAdapter({}).extract_statement(text, "letter.txt", "text/plain")
    assert result.success is False
    assert result.reason == STATEMENT_REASON_NO_LINES


async def test_mock_reports_an_empty_upload():
    result = await MockExtractionAdapter({}).extract_statement(b"")
    assert result.reason == STATEMENT_REASON_EMPTY_FILE


def test_pdf_text_layer_returns_none_on_non_pdf_bytes():
    assert pdf_text_layer(b"this is not a pdf") is None


# --------------------------------------------------------------------------- #
# Model-backed adapters (network stubbed)
# --------------------------------------------------------------------------- #


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient used as an async context manager."""

    def __init__(self, response=None, raises: Exception | None = None):
        self._response = response
        self._raises = raises
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if self._raises is not None:
            raise self._raises
        return self._response


def _patch_httpx(monkeypatch, module, client: _FakeClient):
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kw: client)
    return client


_MODEL_JSON = {
    "lines": [
        {
            "invoice_number": {"value": "INV-1001", "confidence": 0.96},
            "invoice_date": {"value": "2026-01-15", "confidence": 0.9},
            "amount": {"value": "1200.00", "confidence": 0.97},
            "status": {"value": "open", "confidence": 0.5},
        }
    ]
}


async def test_claude_vision_extract_statement_happy_path(monkeypatch):
    from app.services.extraction_adapters import claude_vision as mod

    client = _patch_httpx(
        monkeypatch,
        mod,
        _FakeClient(
            _FakeResponse(
                200,
                {"content": [{"type": "text", "text": f"```json\n{json.dumps(_MODEL_JSON)}\n```"}]},
            )
        ),
    )
    result = await ClaudeVisionAdapter({"api_key": "k"}).extract_statement(
        b"%PDF-1.4 fake", "s.pdf", "application/pdf"
    )
    assert result.success is True
    assert result.lines[0].invoice_number == "INV-1001"
    assert result.lines[0].amount == "1200.00"
    # The statement prompt went out — not the invoice one.
    sent = client.calls[0]["json"]["messages"][0]["content"][1]["text"]
    assert "STATEMENT OF OPEN ITEMS" in sent


async def test_claude_vision_statement_provider_error_stays_out_of_the_reason(monkeypatch):
    from app.services.extraction_adapters import claude_vision as mod

    _patch_httpx(monkeypatch, mod, _FakeClient(_FakeResponse(500, {"error": "boom"})))
    result = await ClaudeVisionAdapter({"api_key": "k"}).extract_statement(b"%PDF-1.4")
    assert result.success is False
    assert result.reason == STATEMENT_REASON_PROVIDER_ERROR
    # The provider's own body must never ride along to the caller's message.
    assert "boom" not in (result.error or "")


async def test_claude_vision_statement_transport_failure_is_not_raised(monkeypatch):
    from app.services.extraction_adapters import claude_vision as mod

    _patch_httpx(monkeypatch, mod, _FakeClient(raises=RuntimeError("connection reset")))
    result = await ClaudeVisionAdapter({"api_key": "k"}).extract_statement(b"%PDF-1.4")
    assert result.reason == STATEMENT_REASON_PROVIDER_ERROR


async def test_claude_vision_statement_unparseable_response(monkeypatch):
    from app.services.extraction_adapters import claude_vision as mod

    _patch_httpx(
        monkeypatch,
        mod,
        _FakeClient(_FakeResponse(200, {"content": [{"type": "text", "text": "sorry, no."}]})),
    )
    result = await ClaudeVisionAdapter({"api_key": "k"}).extract_statement(b"%PDF-1.4")
    assert result.reason == STATEMENT_REASON_UNREADABLE


async def test_ollama_extract_statement_uses_the_text_layer_when_present(monkeypatch):
    from app.services.extraction_adapters import ollama as mod

    monkeypatch.setattr(OllamaAdapter, "_extract_pdf_text", staticmethod(lambda _: _STATEMENT_TEXT))
    client = _patch_httpx(
        monkeypatch,
        mod,
        _FakeClient(_FakeResponse(200, {"message": {"content": json.dumps(_MODEL_JSON)}})),
    )
    result = await OllamaAdapter({}).extract_statement(b"%PDF-1.4", "s.pdf", "application/pdf")
    assert result.success is True
    assert result.lines[0].invoice_number == "INV-1001"
    message = client.calls[0]["json"]["messages"][0]
    assert "images" not in message, "a text-layer PDF must not be sent as page images"
    assert "STATEMENT OF OPEN ITEMS" in message["content"]


async def test_ollama_extract_statement_falls_back_to_page_images(monkeypatch):
    from app.services.extraction_adapters import ollama as mod

    monkeypatch.setattr(OllamaAdapter, "_extract_pdf_text", staticmethod(lambda _: None))
    monkeypatch.setattr(OllamaAdapter, "_pdf_to_images", staticmethod(lambda b, **kw: [b"png"]))
    client = _patch_httpx(
        monkeypatch,
        mod,
        _FakeClient(_FakeResponse(200, {"message": {"content": json.dumps(_MODEL_JSON)}})),
    )
    result = await OllamaAdapter({}).extract_statement(b"%PDF-1.4", "s.pdf", "application/pdf")
    assert result.success is True
    assert client.calls[0]["json"]["messages"][0]["images"]


async def test_ollama_extract_statement_unreadable_pdf(monkeypatch):
    monkeypatch.setattr(OllamaAdapter, "_extract_pdf_text", staticmethod(lambda _: None))
    monkeypatch.setattr(OllamaAdapter, "_pdf_to_images", staticmethod(lambda b, **kw: []))
    result = await OllamaAdapter({}).extract_statement(b"%PDF-1.4", "s.pdf", "application/pdf")
    assert result.success is False
    assert result.reason == STATEMENT_REASON_NO_TEXT_LAYER
