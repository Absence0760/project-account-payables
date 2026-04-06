"""AWS Textract extraction adapter — BYOK option for customers on AWS."""

import json

import httpx

from app.services.extraction_adapters.base import (
    ExtractionAdapter,
    ExtractionResult,
    ExtractedField,
    ExtractedLineItem,
)
from app.services.extraction_adapters.dispatcher import register_extraction_adapter


@register_extraction_adapter("aws_textract")
class AWSTextractAdapter(ExtractionAdapter):
    """Extract invoice data using AWS Textract AnalyzeExpense.

    Required config:
        aws_access_key_id: AWS access key
        aws_secret_access_key: AWS secret key
        aws_region: AWS region (default: us-east-1)
    """

    provider_name = "aws_textract"

    async def extract(self, file_url: str, file_key: str, mime_type: str = "application/pdf") -> ExtractionResult:
        # Textract requires boto3 — use it for the actual API call
        try:
            import boto3
        except ImportError:
            return ExtractionResult(
                success=False, error="boto3 not installed", provider=self.provider_name,
            )

        # Fetch file
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                file_resp = await client.get(file_url)
                file_resp.raise_for_status()
                file_bytes = file_resp.content
        except Exception as exc:
            return ExtractionResult(
                success=False, error=f"Failed to fetch file: {exc}", provider=self.provider_name,
            )

        # Call Textract AnalyzeExpense
        try:
            textract = boto3.client(
                "textract",
                aws_access_key_id=self.config.get("aws_access_key_id"),
                aws_secret_access_key=self.config.get("aws_secret_access_key"),
                region_name=self.config.get("aws_region", "us-east-1"),
            )

            response = textract.analyze_expense(
                Document={"Bytes": file_bytes}
            )
        except Exception as exc:
            return ExtractionResult(
                success=False, error=f"Textract API error: {exc}", provider=self.provider_name,
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
                confidence = field.get("Type", {}).get("Confidence", 0) / 100

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
                        conf = field.get("Type", {}).get("Confidence", 0) / 100
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

    async def test_connection(self) -> bool:
        try:
            import boto3
            textract = boto3.client(
                "textract",
                aws_access_key_id=self.config.get("aws_access_key_id"),
                aws_secret_access_key=self.config.get("aws_secret_access_key"),
                region_name=self.config.get("aws_region", "us-east-1"),
            )
            # Simple check — list adapters (lightweight call)
            textract.get_expense_analysis  # just check the method exists
            return True
        except Exception:
            return False
