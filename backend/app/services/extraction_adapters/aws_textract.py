"""AWS Textract extraction adapter — BYOK option for customers on AWS.

Textract is the one extraction provider with no async client: every other
adapter in this registry (and in the other twenty) talks to its provider over
``httpx.AsyncClient``, but boto3 is synchronous. Constructing the client
resolves the credential chain (which can reach the instance-metadata endpoint)
and ``analyze_expense`` is a full HTTPS round trip against a multi-second OCR
service — so calling either inline from an ``async def`` occupies the event
loop for that whole window and every other in-flight request on the worker
waits behind it. Both are handed to ``asyncio.to_thread``, the same treatment
``services/storage``'s ``_put_object`` and the three ``*_dispatch`` SQS sends
already get. ``tests/test_sqs_dispatch_nonblocking.py`` is the drift guard.
"""

import asyncio

from app.services.extraction_adapters.base import (
    ExtractedField,
    ExtractedLineItem,
    ExtractionAdapter,
    ExtractionResult,
    coerce_confidence,
)
from app.services.extraction_adapters.dispatcher import register_extraction_adapter


def _field_confidence(field: dict) -> float:
    """How much to trust this Textract summary/line field, in 0.0-1.0.

    Textract reports TWO confidences per field and they answer different
    questions: ``Type.Confidence`` is "is this field the TOTAL?" and
    ``ValueDetection.Confidence`` is "does it really say 1500.00?". This adapter
    read only the first, so a crisply-classified but barely-legible figure —
    type 99.5, value 41.0 — arrived as 0.995. That is the number the
    auto-approve gate and the per-field review flags key off, so a total the OCR
    was 41% sure of presented as a 99% read.

    Both must hold for the mapped field to be worth trusting, so take the
    lower. Textract's scale is 0-100; the shared ``coerce_confidence`` bounds
    the result (a missing key is 0 — conservative, and the right default when
    we cannot tell how good a read was).
    """
    type_conf = field.get("Type", {}).get("Confidence", 0)
    value_conf = field.get("ValueDetection", {}).get("Confidence", 0)
    try:
        lower = min(float(type_conf), float(value_conf))
    except (TypeError, ValueError):
        return 0.0
    return coerce_confidence(lower / 100)


@register_extraction_adapter("aws_textract")
class AWSTextractAdapter(ExtractionAdapter):
    """Extract invoice data using AWS Textract AnalyzeExpense.

    Required config:
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key
        aws_region: AWS region (default: us-east-1)
    """

    provider_name = "aws_textract"

    async def extract(
        self,
        file_bytes: bytes = b"",
        file_key: str = "",
        mime_type: str = "application/pdf",
        file_url: str = "",
    ) -> ExtractionResult:
        try:
            import boto3
        except ImportError:
            return ExtractionResult(
                success=False, error="boto3 not installed", provider=self.provider_name
            )

        if not file_bytes:
            return ExtractionResult(
                success=False, error="No file bytes provided", provider=self.provider_name
            )

        # Call Textract AnalyzeExpense — off the loop thread (see module docstring).
        try:
            response = await asyncio.to_thread(self._analyze_expense, boto3, file_bytes)
        except Exception as exc:
            return ExtractionResult(
                success=False,
                error=f"Textract API error: {exc}",
                provider=self.provider_name,
            )

        # Parse Textract response
        result = ExtractionResult(
            success=True,
            raw_response=response,
            provider=self.provider_name,
        )

        # Extract summary fields from Textract response
        field_map = {
            "INVOICE_RECEIPT_ID": "invoice_number",
            "VENDOR_NAME": "vendor_name",
            "VENDOR_ADDRESS": "vendor_address",
            "TOTAL": "amount",
            "SUBTOTAL": "subtotal",
            "TAX": "tax_amount",
            "INVOICE_RECEIPT_DATE": "invoice_date",
            "DUE_DATE": "due_date",
            "PO_NUMBER": "po_number",
            "PAYMENT_TERMS": "payment_terms",
        }

        for doc in response.get("ExpenseDocuments", []):
            for field in doc.get("SummaryFields", []):
                field_type = field.get("Type", {}).get("Text", "")
                value_obj = field.get("ValueDetection", {})
                value = value_obj.get("Text")
                confidence = _field_confidence(field)

                attr_name = field_map.get(field_type)
                if attr_name and value:
                    setattr(result, attr_name, ExtractedField(value, confidence))

            # Extract line items
            for group in doc.get("LineItemGroups", []):
                for idx, item in enumerate(group.get("LineItems", [])):
                    li = ExtractedLineItem(line_number=idx + 1)
                    for field in item.get("LineItemExpenseFields", []):
                        field_type = field.get("Type", {}).get("Text", "")
                        value = field.get("ValueDetection", {}).get("Text")
                        conf = _field_confidence(field)
                        if field_type == "ITEM" and value:
                            li.description = ExtractedField(value, conf)
                        elif field_type == "QUANTITY" and value:
                            li.quantity = ExtractedField(value, conf)
                        elif field_type == "UNIT_PRICE" and value:
                            li.unit_price = ExtractedField(value, conf)
                        elif field_type == "PRICE" and value:
                            li.total = ExtractedField(value, conf)
                    result.line_items.append(li)

        # Overall confidence
        fields = [result.invoice_number, result.vendor_name, result.amount]
        confidences = [f.confidence for f in fields if f.value is not None]
        result.overall_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return result

    def _client(self, boto3):
        """Build the Textract client. **Synchronous** — call it only from a
        worker thread: boto3 resolves the credential chain here, which loads
        botocore's on-disk service model and can reach the instance-metadata
        endpoint."""
        from app.config import settings

        return boto3.client(
            "textract",
            aws_access_key_id=self.config.get("aws_access_key_id"),
            aws_secret_access_key=self.config.get("aws_secret_access_key"),
            endpoint_url=settings.aws_endpoint_url or None,
            region_name=self.config.get("aws_region", "us-east-1"),
        )

    def _analyze_expense(self, boto3, file_bytes: bytes) -> dict:
        """The blocking Textract round trip. Runs on a worker thread."""
        return self._client(boto3).analyze_expense(Document={"Bytes": file_bytes})

    def _probe(self, boto3) -> bool:
        """The blocking connection probe. Runs on a worker thread."""
        client = self._client(boto3)
        # Cheapest possible check — the client exposes the operation we need.
        # No network call: a real probe would cost an OCR job.
        return hasattr(client, "get_expense_analysis")

    async def test_connection(self) -> bool:
        """Probe the credentials. Awaited directly by
        ``POST /api/organization/test-extraction`` on the request path, so the
        blocking client construction goes to a worker thread."""
        try:
            import boto3

            return await asyncio.to_thread(self._probe, boto3)
        except Exception:
            return False
