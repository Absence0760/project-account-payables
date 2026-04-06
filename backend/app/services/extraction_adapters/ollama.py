"""Ollama local extraction adapter — runs AI models locally via Ollama.

Supports LLaVA, Llama 3.2 Vision, and other Ollama vision models.
No API key needed — runs on localhost.

Setup:
    brew install ollama
    ollama pull llama3.2-vision:11b
"""

import base64
import json

import httpx

from app.services.extraction_adapters.base import (
    ExtractionAdapter,
    ExtractionResult,
    ExtractedField,
    ExtractedLineItem,
)
from app.services.extraction_adapters.claude_vision import EXTRACTION_PROMPT, _parse_field
from app.services.extraction_adapters.dispatcher import register_extraction_adapter


@register_extraction_adapter("ollama")
class OllamaAdapter(ExtractionAdapter):
    """Extract invoice data using a local Ollama vision model.

    Config:
        base_url: Ollama API URL (default: http://localhost:11434)
        model: Model name (default: llama3.2-vision:11b)
    """

    provider_name = "ollama"

    def _base_url(self) -> str:
        return self.config.get("base_url", "http://localhost:11434")

    def _model(self) -> str:
        return self.config.get("model", "llama3.2-vision:11b")

    async def extract(self, file_bytes: bytes = b"", file_key: str = "", mime_type: str = "application/pdf", file_url: str = "") -> ExtractionResult:
        if not file_bytes:
            return ExtractionResult(success=False, error="No file bytes provided", provider=self.provider_name)

        file_b64 = base64.b64encode(file_bytes).decode("utf-8")

        # If PDF, we need to note that Ollama vision models work with images
        # For PDFs, the caller should convert to image first (future improvement)
        # For now, send as-is — some models handle it, others won't

        # Call Ollama API (OpenAI-compatible endpoint)
        body = {
            "model": self._model(),
            "messages": [
                {
                    "role": "user",
                    "content": EXTRACTION_PROMPT,
                    "images": [file_b64],
                }
            ],
            "stream": False,
            "format": "json",
        }

        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self._base_url()}/api/chat",
                    json=body,
                )
        except httpx.ConnectError:
            return ExtractionResult(
                success=False,
                error="Cannot connect to Ollama. Is it running? Start with: ollama serve",
                provider=self.provider_name,
            )
        except Exception as exc:
            return ExtractionResult(
                success=False, error=f"Ollama API error: {exc}", provider=self.provider_name,
            )

        if resp.status_code != 200:
            return ExtractionResult(
                success=False,
                error=f"Ollama error {resp.status_code}: {resp.text}",
                provider=self.provider_name,
            )

        resp_data = resp.json()
        text_content = resp_data.get("message", {}).get("content", "")

        # Parse JSON from response
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
                error="Failed to parse JSON from Ollama response",
                raw_response={"text": text_content},
                provider=self.provider_name,
            )

        # Build result (same parsing as Claude adapter)
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

        for li in data.get("line_items", []):
            result.line_items.append(ExtractedLineItem(
                line_number=li.get("line_number", 0),
                item_code=_parse_field(li, "item_code"),
                description=_parse_field(li, "description"),
                quantity=_parse_field(li, "quantity"),
                unit_price=_parse_field(li, "unit_price"),
                tax=_parse_field(li, "tax"),
                total=_parse_field(li, "total"),
            ))

        fields = [result.invoice_number, result.vendor_name, result.amount]
        confidences = [f.confidence for f in fields if f.value is not None]
        result.overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return result

    async def test_connection(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url()}/api/tags")
            if resp.status_code != 200:
                return False
            # Check if the configured model is available
            models = [m["name"] for m in resp.json().get("models", [])]
            target = self._model()
            return any(target in m for m in models)
        except Exception:
            return False
