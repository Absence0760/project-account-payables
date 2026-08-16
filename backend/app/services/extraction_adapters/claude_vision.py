"""Claude Vision extraction adapter — uses Anthropic's Claude API for invoice OCR.

This is the platform default. Handles PDFs and images with structured extraction prompts.
"""

import base64
import json

import httpx

from app.services.extraction_adapters.base import (
    STATEMENT_REASON_EMPTY_FILE,
    STATEMENT_REASON_PROVIDER_ERROR,
    STATEMENT_REASON_UNREADABLE,
    ExtractedField,
    ExtractedLineItem,
    ExtractionAdapter,
    ExtractionResult,
    StatementExtractionResult,
)
from app.services.extraction_adapters.dispatcher import register_extraction_adapter
from app.services.extraction_adapters.statement_extraction import (
    STATEMENT_EXTRACTION_PROMPT,
    parse_statement_payload,
)

_EXTRACTION_PROMPT_TEMPLATE = """You are an invoice data extraction system. \
Extract all fields from this invoice image/document.

Return a JSON object with the following structure. For each field, \
provide the value and a confidence score between 0.0 and 1.0. \
Use these confidence ranges:
- 0.95-1.0: field is clearly printed and unambiguous
- 0.8-0.94: field is legible but could have a minor read error
- 0.5-0.79: field is partially obscured, blurry, or you are guessing
- 0.1-0.49: field is barely visible or mostly inferred from context
- null value with 0.0: field is not present on the document

Do NOT default to 1.0 for all fields. Be honest about uncertainty.

```json
{
  "invoice_number": {"value": "string", "confidence": 0.95},
  "vendor_name": {"value": "string", "confidence": 0.9},
  "vendor_address": {"value": "string", "confidence": 0.85},
  "vendor_tax_id": {"value": "string or null", "confidence": 0.7},
  "amount": {"value": "decimal string", "confidence": 0.95},
  "currency": {"value": "3-letter code", "confidence": 0.9},
  "subtotal": {"value": "decimal string or null", "confidence": 0.85},
  "tax_amount": {"value": "decimal string or null", "confidence": 0.8},
  "tax_rate": {"value": "decimal string or null", "confidence": 0.6},
  "discount_amount": {"value": "decimal string or null", "confidence": 0.5},
  "shipping_amount": {"value": "decimal string or null", "confidence": 0.5},
  "invoice_date": {"value": "YYYY-MM-DD", "confidence": 0.95},
  "due_date": {"value": "YYYY-MM-DD or null", "confidence": 0.85},
  "payment_terms": {"value": "string or null", "confidence": 0.7},
  "po_number": {"value": "string or null", "confidence": 0.8},
  "description": {"value": "brief description of invoice contents", "confidence": 0.75},
  "reference_number": {"value": "string or null", "confidence": 0.6},
  "payment_method": {"value": "ach|wire|check|credit_card or null", "confidence": 0.5},
  "bill_to_address": {"value": "string or null", "confidence": 0.7},
  "remit_to_address": {"value": "string or null", "confidence": 0.6},
  "suggested_gl_account": {"value": "GL code or null", "confidence": 0.5},
  "suggested_cost_center": {"value": "cost center or null", "confidence": 0.4},
  "line_items": [
    {
      "line_number": 1,
      "item_code": {"value": "string or null", "confidence": 0.7},
      "description": {"value": "string", "confidence": 0.85},
      "quantity": {"value": "decimal string", "confidence": 0.9},
      "unit_price": {"value": "decimal string", "confidence": 0.9},
      "tax": {"value": "decimal string or null", "confidence": 0.6},
      "total": {"value": "decimal string", "confidence": 0.95}
    }
  ]
}
```

For GL account suggestion, use ONLY codes from this chart of accounts:
{{GL_ACCOUNT_CATALOG}}

Return ONLY the JSON object, no other text."""

_GL_PLACEHOLDER = "{{GL_ACCOUNT_CATALOG}}"

_DEFAULT_GL_LIST = """\
- Office supplies → 6100
- Cloud/software services → 6200
- Facility/maintenance → 6300
- Marketing → 6400
- Legal/professional → 6500
- Food/catering → 6600
- Shipping/logistics → 6700
- Hardware/equipment → 1500"""

# Backward-compatible constant with the default GL list baked in.
# Other adapters (openai_vision, ollama) import this directly.
EXTRACTION_PROMPT = _EXTRACTION_PROMPT_TEMPLATE.replace(_GL_PLACEHOLDER, _DEFAULT_GL_LIST)


def _strip_json_fence(text: str) -> str:
    """Unwrap a ```json ... ``` fence the model sometimes adds around its JSON."""
    json_str = text.strip()
    if json_str.startswith("```"):
        json_str = json_str.split("```")[1]
        if json_str.startswith("json"):
            json_str = json_str[4:]
    return json_str.strip()


def _document_block(file_bytes: bytes, mime_type: str) -> dict:
    """Build the Anthropic content block for a PDF page set or a single image."""
    if mime_type in ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"):
        media_type = mime_type
    else:
        media_type = "application/pdf"
    return {
        "type": "document" if media_type == "application/pdf" else "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(file_bytes).decode("utf-8"),
        },
    }


def _parse_field(data: dict | None, field_name: str) -> ExtractedField:
    if not data or field_name not in data:
        return ExtractedField(None, 0.0)
    field = data[field_name]
    if isinstance(field, dict):
        return ExtractedField(field.get("value"), field.get("confidence", 0.0))
    return ExtractedField(str(field), 0.5)


@register_extraction_adapter("claude_vision")
class ClaudeVisionAdapter(ExtractionAdapter):
    """Extract invoice data using Claude's vision capabilities.

    Required config:
        api_key: Anthropic API key
        model: Model to use (default: claude-sonnet-4-20250514)
    """

    provider_name = "claude_vision"

    async def extract(
        self,
        file_bytes: bytes = b"",
        file_key: str = "",
        mime_type: str = "application/pdf",
        file_url: str = "",
    ) -> ExtractionResult:
        api_key = self.config.get("api_key", "")
        model = self.config.get("model", "claude-sonnet-4-20250514")

        if not file_bytes:
            return ExtractionResult(
                success=False, error="No file bytes provided", provider=self.provider_name
            )

        # Determine media type
        if mime_type in ("application/pdf",):
            media_type = "application/pdf"
            source_type = "base64"
        elif mime_type in ("image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"):
            media_type = mime_type
            source_type = "base64"
        else:
            media_type = "application/pdf"
            source_type = "base64"

        file_b64 = base64.b64encode(file_bytes).decode("utf-8")

        # Build the extraction prompt — inject org-specific GL catalog if
        # available, otherwise fall back to the default hardcoded list.
        gl_catalog = self.config.get("gl_account_catalog")
        if gl_catalog:
            base_prompt = _EXTRACTION_PROMPT_TEMPLATE.replace(_GL_PLACEHOLDER, gl_catalog)
        else:
            base_prompt = EXTRACTION_PROMPT

        # RAG few-shot context (retrieved by services.extraction.run_extraction
        # and passed through the adapter config). Prepend as a preamble so the
        # extraction prompt that follows stays authoritative.
        few_shot = self.config.get("few_shot_prompt") or ""
        prompt_text = base_prompt
        if few_shot:
            prompt_text = f"{few_shot}\n\n---\n\n{base_prompt}"

        # Call Claude API
        body = {
            "model": model,
            "max_tokens": 4096,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document" if media_type == "application/pdf" else "image",
                            "source": {
                                "type": source_type,
                                "media_type": media_type,
                                "data": file_b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt_text,
                        },
                    ],
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=body,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                )
        except Exception as exc:
            return ExtractionResult(
                success=False,
                error=f"API call failed: {exc}",
                provider=self.provider_name,
            )

        if resp.status_code != 200:
            return ExtractionResult(
                success=False,
                error=f"Claude API error {resp.status_code}: {resp.text}",
                provider=self.provider_name,
            )

        # Parse response
        resp_data = resp.json()
        text_content = ""
        for block in resp_data.get("content", []):
            if block.get("type") == "text":
                text_content += block.get("text", "")

        # Extract JSON from response (may be wrapped in ```json ... ```)
        json_str = text_content.strip()
        if json_str.startswith("```"):
            json_str = json_str.split("```")[1]
            if json_str.startswith("json"):
                json_str = json_str[4:]
        json_str = json_str.strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return ExtractionResult(
                success=False,
                error="Failed to parse JSON from Claude response",
                raw_response={"text": text_content},
                provider=self.provider_name,
            )

        # Build result
        result = ExtractionResult(
            success=True,
            invoice_number=_parse_field(data, "invoice_number"),
            vendor_name=_parse_field(data, "vendor_name"),
            vendor_address=_parse_field(data, "vendor_address"),
            vendor_tax_id=_parse_field(data, "vendor_tax_id"),
            amount=_parse_field(data, "amount"),
            currency=_parse_field(data, "currency"),
            subtotal=_parse_field(data, "subtotal"),
            tax_amount=_parse_field(data, "tax_amount"),
            tax_rate=_parse_field(data, "tax_rate"),
            discount_amount=_parse_field(data, "discount_amount"),
            shipping_amount=_parse_field(data, "shipping_amount"),
            invoice_date=_parse_field(data, "invoice_date"),
            due_date=_parse_field(data, "due_date"),
            payment_terms=_parse_field(data, "payment_terms"),
            po_number=_parse_field(data, "po_number"),
            description=_parse_field(data, "description"),
            reference_number=_parse_field(data, "reference_number"),
            payment_method=_parse_field(data, "payment_method"),
            bill_to_address=_parse_field(data, "bill_to_address"),
            remit_to_address=_parse_field(data, "remit_to_address"),
            suggested_gl_account=_parse_field(data, "suggested_gl_account"),
            suggested_cost_center=_parse_field(data, "suggested_cost_center"),
            raw_response=data,
            provider=self.provider_name,
        )

        # Parse line items
        for li in data.get("line_items", []):
            result.line_items.append(
                ExtractedLineItem(
                    line_number=li.get("line_number", 0),
                    item_code=_parse_field(li, "item_code"),
                    description=_parse_field(li, "description"),
                    quantity=_parse_field(li, "quantity"),
                    unit_price=_parse_field(li, "unit_price"),
                    tax=_parse_field(li, "tax"),
                    total=_parse_field(li, "total"),
                )
            )

        # Calculate overall confidence
        fields = [
            result.invoice_number,
            result.vendor_name,
            result.amount,
            result.invoice_date,
            result.due_date,
        ]
        confidences = [f.confidence for f in fields if f.value is not None]
        result.overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return result

    async def extract_statement(
        self,
        file_bytes: bytes = b"",
        file_key: str = "",
        mime_type: str = "application/pdf",
    ) -> StatementExtractionResult:
        """Read a supplier statement of open items via Claude's vision path.

        Same document channel as :meth:`extract` — only the prompt and the
        response shape differ, because a statement is many rows for one
        supplier rather than one invoice header. Never raises: a transport or
        provider failure comes back as ``success=False`` with a PII-free
        ``reason``; the provider's own text stays on ``error`` for the log.
        """
        if not file_bytes:
            return StatementExtractionResult(
                available=True, provider=self.provider_name, reason=STATEMENT_REASON_EMPTY_FILE
            )

        body = {
            "model": self.config.get("model", "claude-sonnet-4-20250514"),
            "max_tokens": 8192,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        _document_block(file_bytes, mime_type),
                        {"type": "text", "text": STATEMENT_EXTRACTION_PROMPT},
                    ],
                }
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json=body,
                    headers={
                        "x-api-key": self.config.get("api_key", ""),
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                )
        except Exception as exc:
            return StatementExtractionResult(
                available=True,
                provider=self.provider_name,
                reason=STATEMENT_REASON_PROVIDER_ERROR,
                error=f"API call failed: {exc}",
            )

        if resp.status_code != 200:
            return StatementExtractionResult(
                available=True,
                provider=self.provider_name,
                reason=STATEMENT_REASON_PROVIDER_ERROR,
                error=f"Claude API error {resp.status_code}",
            )

        text_content = ""
        for block in resp.json().get("content", []):
            if block.get("type") == "text":
                text_content += block.get("text", "")

        try:
            data = json.loads(_strip_json_fence(text_content))
        except json.JSONDecodeError:
            return StatementExtractionResult(
                available=True,
                provider=self.provider_name,
                reason=STATEMENT_REASON_UNREADABLE,
                error="Failed to parse JSON from Claude response",
            )

        return parse_statement_payload(data, self.provider_name)

    async def test_connection(self) -> bool:
        try:
            api_key = self.config.get("api_key", "")
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    json={
                        "model": "claude-sonnet-4-20250514",
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": "ping"}],
                    },
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                )
            return resp.status_code == 200
        except Exception:
            return False
