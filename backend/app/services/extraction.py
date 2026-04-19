"""AI invoice extraction service.

Uses the extraction adapter pattern — supports platform (Claude Vision) and BYOK
(OpenAI, Textract, or customer's own Anthropic key) modes.

Tracks usage for billing when platform mode is used.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.invoice import Invoice, InvoiceExtractionResult, InvoiceLineItem, InvoiceStatus
from app.models.usage import ExtractionUsage
from app.services.extraction_adapters.base import ExtractionResult
from app.services.workflow_engine import (
    advance_workflow,
    get_workflow_instance,
    transition_invoice,
)


def _resolve_extraction_config(org_settings: dict | None) -> dict:
    """Build extraction adapter config based on org settings and program type."""
    extraction = (org_settings or {}).get("extraction", {})
    program_type = extraction.get("program_type", "platform")

    if program_type == "byok":
        return extraction
    else:
        # Platform mode — use app-level keys
        return {
            "program_type": "platform",
            "provider": "claude_vision",
            "api_key": settings.anthropic_api_key,
            "model": settings.extraction_model,
        }


async def run_extraction(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID | None = None,
    org_settings: dict | None = None,
) -> None:
    """Extract invoice fields from the uploaded file and update the invoice.

    This is called as a background task after file upload.
    """
    # Cache IDs before try block — after rollback, invoice attrs may be expired
    invoice_id = invoice.id
    invoice_org_id = invoice.organization_id

    try:
        config = _resolve_extraction_config(org_settings)

        # Import adapters to trigger registration
        import app.services.extraction_adapters.aws_textract  # noqa: F401
        import app.services.extraction_adapters.claude_vision  # noqa: F401
        import app.services.extraction_adapters.mock_adapter  # noqa: F401
        import app.services.extraction_adapters.ollama  # noqa: F401
        import app.services.extraction_adapters.openai_vision  # noqa: F401
        from app.services.extraction_adapters import get_extraction_adapter

        adapter = get_extraction_adapter(config)
        print(
            f"[extraction] Using adapter: {adapter.provider_name}, "
            f"config provider: {config.get('provider')}"
        )

        # Fetch file bytes from S3/MinIO directly (authenticated)
        import boto3 as _boto3

        from app.config import settings as app_settings

        s3 = _boto3.client(
            "s3",
            endpoint_url=app_settings.s3_endpoint_url,
            aws_access_key_id=app_settings.s3_access_key,
            aws_secret_access_key=app_settings.s3_secret_key,
        )
        file_key = invoice.file_key or ""
        print(
            f"[extraction] Fetching file from S3: bucket={app_settings.s3_bucket}, key={file_key}"
        )
        s3_obj = s3.get_object(Bucket=app_settings.s3_bucket, Key=file_key)
        file_bytes = s3_obj["Body"].read()
        print(f"[extraction] File fetched: {len(file_bytes)} bytes")

        result = await adapter.extract(
            file_bytes=file_bytes,
            file_key=file_key,
            mime_type="application/pdf",
        )

        if not result.success:
            print(f"[extraction] Adapter returned failure: {result.error}")
            raise RuntimeError(result.error or "Extraction failed")

        print(
            f"[extraction] Success! Confidence: "
            f"{result.overall_confidence}, "
            f"vendor: {result.vendor_name.value}"
        )

        # Apply extracted fields to invoice
        _apply_extraction(invoice, result)

        # Save line items if extracted
        for li in result.line_items:
            line_item = InvoiceLineItem(
                invoice_id=invoice.id,
                line_number=li.line_number,
                item_code=li.item_code.value if li.item_code.value else None,
                description=li.description.value if li.description.value else None,
                quantity=_clean_decimal(li.quantity.value) if li.quantity.value else None,
                unit_price=_clean_decimal(li.unit_price.value) if li.unit_price.value else None,
                tax=_clean_decimal(li.tax.value) if li.tax.value else None,
                total=_clean_decimal(li.total.value) if li.total.value else None,
                gl_account=li.gl_account.value if li.gl_account.value else None,
            )
            db.add(line_item)

        # Apply AI-suggested GL coding
        if result.suggested_gl_account.value and result.suggested_gl_account.confidence >= 0.7:
            invoice.gl_account = result.suggested_gl_account.value
        if result.suggested_cost_center.value and result.suggested_cost_center.confidence >= 0.7:
            invoice.cost_center = result.suggested_cost_center.value

        # Vendor matching
        from app.services.vendor_matching import match_and_link_vendor

        vendor, vendor_action = await match_and_link_vendor(
            db,
            invoice,
            invoice.organization_id,
        )

        # Per-vendor correction cache: overlay cached priors (currency, tax
        # rate, payment terms, etc.) onto low-confidence extracted fields
        # for this vendor. No-op if the vendor is new or has no priors yet.
        from app.services.vendor_priors import apply_priors_to_invoice

        applied_priors = await apply_priors_to_invoice(db, invoice, result)
        if applied_priors:
            print(f"[extraction] Applied vendor priors: {applied_priors}")

        # Save extraction result
        extraction_result = InvoiceExtractionResult(
            invoice_id=invoice.id,
            method=result.provider or config.get("provider", "unknown"),
            confidence=Decimal(str(round(result.overall_confidence, 4))),
            raw_result=result.raw_response,
        )
        db.add(extraction_result)

        # Track usage for billing
        program_type = config.get("program_type", "platform")
        usage = ExtractionUsage(
            invoice_id=invoice.id,
            provider=result.provider or config.get("provider", "unknown"),
            program_type=program_type,
            period=datetime.now(UTC).strftime("%Y-%m"),
            success=True,
            organization_id=invoice.organization_id,
        )
        db.add(usage)

        # Transition pending → ready_for_review
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.ready_for_review,
            actor_id=actor_id,
            action_name="invoice.extraction_completed",
            details={
                "method": result.provider,
                "confidence": result.overall_confidence,
                "vendor_action": vendor_action,
                "vendor_id": str(vendor.id) if vendor else None,
                "gl_suggested": result.suggested_gl_account.value,
                "program_type": program_type,
            },
        )

        # Advance workflow to review step
        instance = await get_workflow_instance(db, invoice.id)
        if instance:
            await advance_workflow(db, instance, "review", action="extracted")

        await db.commit()

    except Exception as exc:
        print(f"[extraction] Failed: {exc}")
        await db.rollback()

        # Re-fetch invoice after rollback (the old object is expired)
        from sqlalchemy import select as sa_select

        from app.models.invoice import Invoice as InvoiceModel

        result = await db.execute(sa_select(InvoiceModel).where(InvoiceModel.id == invoice_id))
        invoice = result.scalar_one_or_none()
        if not invoice:
            await db.commit()
            return

        # Track failed usage
        try:
            config = _resolve_extraction_config(org_settings)
            usage = ExtractionUsage(
                invoice_id=invoice_id,
                provider=config.get("provider", "unknown"),
                program_type=config.get("program_type", "platform"),
                period=datetime.now(UTC).strftime("%Y-%m"),
                success=False,
                organization_id=invoice_org_id,
            )
            db.add(usage)
        except Exception:
            pass

        # Create exception record
        from app.models.exception import Exception as APException

        db.add(
            APException(
                invoice_id=invoice_id,
                exception_type="extraction_failed",
                severity="error",
                description=f"Extraction failed: {str(exc)[:500]}",
                status="open",
                organization_id=invoice_org_id,
            )
        )

        # Transition pending → failed
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.failed,
            actor_id=actor_id,
            action_name="invoice.extraction_failed",
            details={"error": str(exc)},
        )

        instance = await get_workflow_instance(db, invoice_id)
        if instance:
            instance.state = "failed"
            instance.state_data = {**(instance.state_data or {}), "error": str(exc)}

        await db.commit()


def _clean_decimal(val: str) -> Decimal | None:
    """Clean a string value for Decimal conversion — strips $, commas, spaces."""
    if not val:
        return None
    cleaned = val.strip().replace("$", "").replace(",", "").replace(" ", "")
    if cleaned in ("", "null", "None", "N/A", "n/a", "-"):
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


def _apply_extraction(invoice: Invoice, result: ExtractionResult) -> None:
    """Apply extracted fields to the invoice record."""
    if result.invoice_number.value:
        invoice.invoice_number = result.invoice_number.value
    if result.vendor_name.value:
        invoice.vendor_name = result.vendor_name.value
    if result.amount.value:
        d = _clean_decimal(result.amount.value)
        if d is not None:
            invoice.amount = d
    if result.currency.value:
        invoice.currency = result.currency.value
    if result.subtotal.value:
        d = _clean_decimal(result.subtotal.value)
        if d is not None:
            invoice.subtotal = d
    if result.tax_amount.value:
        d = _clean_decimal(result.tax_amount.value)
        if d is not None:
            invoice.tax_amount = d
    if result.tax_rate.value:
        d = _clean_decimal(result.tax_rate.value)
        if d is not None:
            invoice.tax_rate = d
    if result.discount_amount.value:
        d = _clean_decimal(result.discount_amount.value)
        if d is not None:
            invoice.discount_amount = d
    if result.shipping_amount.value:
        d = _clean_decimal(result.shipping_amount.value)
        if d is not None:
            invoice.shipping_amount = d
    if result.invoice_date.value:
        from datetime import date as date_type

        try:
            invoice.invoice_date = date_type.fromisoformat(result.invoice_date.value)
        except ValueError:
            pass
    if result.due_date.value:
        from datetime import date as date_type

        try:
            invoice.due_date = date_type.fromisoformat(result.due_date.value)
        except ValueError:
            pass
    if result.payment_terms.value:
        invoice.payment_terms = result.payment_terms.value
    if result.po_number.value:
        invoice.po_number = result.po_number.value
    if result.description.value:
        invoice.description = result.description.value
    if result.vendor_address.value:
        invoice.vendor_address = result.vendor_address.value
    if result.vendor_tax_id.value:
        invoice.vendor_tax_id = result.vendor_tax_id.value
    if result.payment_method.value:
        invoice.payment_method = result.payment_method.value
    if result.reference_number.value:
        invoice.reference_number = result.reference_number.value
    if result.bill_to_address.value:
        invoice.bill_to_address = result.bill_to_address.value
    if result.remit_to_address.value:
        invoice.remit_to_address = result.remit_to_address.value
