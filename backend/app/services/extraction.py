"""AI invoice extraction service.

Uses the extraction adapter pattern — supports platform (Claude Vision) and BYOK
(OpenAI, Textract, or customer's own Anthropic key) modes.

Tracks usage for billing when platform mode is used.
"""

import uuid
from datetime import UTC, date, datetime
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

        # RAG: embed the invoice text and fetch similar past extractions to
        # prime the adapter. No-op when rag_enabled=False, text layer empty,
        # or the tenant has no embeddings yet.
        from app.services.rag import (
            build_few_shot_prompt,
            extract_invoice_text,
            neighbors_to_metadata,
            retrieve_similar,
        )

        invoice_text = extract_invoice_text(file_bytes)
        neighbors = await retrieve_similar(db, invoice_text, exclude_invoice_id=invoice_id)
        if neighbors:
            config["few_shot_prompt"] = build_few_shot_prompt(neighbors)
            print(f"[extraction] RAG: {len(neighbors)} neighbors injected as few-shot")

        # Build a fresh adapter now that few_shot_prompt is in the config.
        adapter = get_extraction_adapter(config)

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

        # Semantic duplicate detection — reuses invoice_embeddings. Catches
        # near-duplicates that the rule-based (vendor_name + invoice_number)
        # check in invoice_warnings.py misses. Flags with a warning on the
        # invoice and an APException for the queue; never blocks the
        # extraction itself — reviewer decides.
        from app.models.exception import Exception as APException
        from app.services.duplicate_detection import (
            find_semantic_duplicates,
            matches_to_warning,
        )

        duplicate_matches = await find_semantic_duplicates(
            db, invoice_text, exclude_invoice_id=invoice_id
        )
        duplicate_warning = matches_to_warning(duplicate_matches)
        if duplicate_warning:
            existing = list(invoice.warnings or [])
            existing.append(duplicate_warning)
            invoice.warnings = existing
            db.add(
                APException(
                    invoice_id=invoice_id,
                    exception_type="duplicate",
                    severity="warning",
                    description=duplicate_warning["message"],
                    status="open",
                    organization_id=invoice_org_id,
                )
            )
            print(f"[extraction] Duplicate detection: {len(duplicate_matches)} near-match(es)")

        # Refresh warnings + PO match — handles missing fields, duplicates,
        # fraud flags, vendor verification status, and 2/3-way PO matching.
        # Runs here so the reviewer sees a fully-populated invoice (with any
        # exceptions surfaced in the queue) the moment extraction lands.
        from app.services.invoice_warnings import refresh_warnings

        await refresh_warnings(db, invoice)

        # Save extraction result with priors metadata (what the UI will show
        # for transparency — which cache overrides and RAG neighbors shaped
        # this extraction).
        priors_metadata: dict = {}
        if applied_priors:
            priors_metadata["vendor_cache_applied"] = applied_priors
        if neighbors:
            priors_metadata["rag_neighbors"] = neighbors_to_metadata(neighbors)

        extraction_result = InvoiceExtractionResult(
            invoice_id=invoice.id,
            method=result.provider or config.get("provider", "unknown"),
            confidence=Decimal(str(round(result.overall_confidence, 4))),
            raw_result=result.raw_response,
            priors_metadata=priors_metadata or None,
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


# Sentinel values to treat as "no extraction." Vision models routinely
# emit these literal strings (or leak parts of the prompt schema) when a
# field isn't present on the document; landing them verbatim in the DB
# pollutes downstream search, vendor matching, and exception flagging.
_NULL_SENTINELS = frozenset(
    s.casefold()
    for s in (
        "",
        "null",
        "none",
        "n/a",
        "na",
        "-",
        "—",
        "string or null",
        "string",
        "tbd",
        "unknown",
        "not provided",
        "not specified",
        "not available",
    )
)

# Currency symbols stripped before Decimal conversion. We keep the raw
# value's currency code on `invoice.currency`; the amount column is the
# numeric magnitude only.
_CURRENCY_SYMBOLS = "$€£¥₹₽₩฿₪₦"


def _clean_decimal(val: str | None) -> Decimal | None:
    """Clean a string value for Decimal conversion.

    Handles the common failure modes vision models produce:
    - Currency symbols (`$`, `€`, `£`, ...)
    - Thousands separators + spaces
    - Percent signs (e.g. `8.25%` for tax_rate)
    - Parenthesised negatives (`(123.45)` → `-123.45`, classic accounting)
    - Unicode minus sign (`−` U+2212) used by Word-style invoices
    - Sentinel "no value" strings (`null`, `N/A`, `-`, etc.)

    Returns None on anything that can't be parsed — never raises.
    """
    if val is None:
        return None
    s = str(val).strip()
    if s.casefold() in _NULL_SENTINELS:
        return None

    # Accounting negatives: "(123.45)" → "-123.45"
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1].strip()

    # Strip currency symbols + separators
    for sym in _CURRENCY_SYMBOLS:
        s = s.replace(sym, "")
    s = s.replace(",", "").replace(" ", "").replace("%", "")
    # Unicode minus → ASCII minus
    s = s.replace("\u2212", "-")

    if s.casefold() in _NULL_SENTINELS:
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def _clean_string(val: str | None) -> str | None:
    """Filter sentinel "no value" strings out of free-form text fields.

    Same `_NULL_SENTINELS` treatment as `_clean_decimal` so we don't end
    up with literal `"null"` or `"N/A"` in `vendor_address` etc. Returns
    the trimmed string or None.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.casefold() in _NULL_SENTINELS:
        return None
    return s


# Map of common ways models phrase a payment method → our canonical value.
# Keys are casefolded; values match the dropdown options in
# `InvoiceModal.svelte` so a select-bind round-trips cleanly.
_PAYMENT_METHOD_ALIASES = {
    "ach": "ach",
    "ach transfer": "ach",
    "ach payment": "ach",
    "automated clearing house": "ach",
    "wire": "wire",
    "wire transfer": "wire",
    "swift": "wire",
    "check": "check",
    "cheque": "check",
    "paper check": "check",
    "credit card": "credit_card",
    "creditcard": "credit_card",
    "card": "credit_card",
    "credit": "credit_card",
    "cc": "credit_card",
    "virtual card": "credit_card",
    "vcard": "credit_card",
    "other": "other",
    "rtp": "wire",  # real-time payment — no dropdown option, fold into wire
    "ach preferred. wire transfer accepted.": "ach",  # exact prompt-leak we've seen
}


def _normalize_payment_method(val: str | None) -> str | None:
    """Map the model's free-form payment-method string to a canonical
    lowercase value the frontend dropdown matches.

    Without this, an extraction of `"ACH"` (uppercase) leaves the
    dropdown blank because its options are lowercase. Returns None
    when nothing maps and the value isn't already a known canonical form.
    """
    if val is None:
        return None
    s = str(val).strip().casefold()
    if not s or s in _NULL_SENTINELS:
        return None
    if s in _PAYMENT_METHOD_ALIASES:
        return _PAYMENT_METHOD_ALIASES[s]
    # Heuristic: substring match for compound phrases like "ACH preferred."
    for alias, canonical in _PAYMENT_METHOD_ALIASES.items():
        if alias in s:
            return canonical
    return None


def _clean_date(val: str | None) -> date | None:
    """Parse a model-returned date string into a `date`.

    Tries ISO first (the format we ask for in the prompt), then the
    common variations vision models slip into: US `MM/DD/YYYY`,
    European `DD/MM/YYYY` (ambiguous with US — we try US first since
    most invoices we see are US), and human-readable `Month DD, YYYY`.

    Strict `date.fromisoformat` was silently dropping anything but
    `YYYY-MM-DD`, which meant a model returning "March 15, 2024" lost
    the date entirely.
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s or s.casefold() in _NULL_SENTINELS:
        return None

    # ISO is the prompt-requested format — try first.
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass

    # Common alternates, in priority order.
    formats = (
        "%m/%d/%Y",  # 3/15/2024
        "%m-%d-%Y",  # 3-15-2024
        "%d/%m/%Y",  # 15/3/2024 (EU)
        "%d-%m-%Y",
        "%Y/%m/%d",  # 2024/3/15
        "%B %d, %Y",  # March 15, 2024
        "%b %d, %Y",  # Mar 15, 2024
        "%d %B %Y",  # 15 March 2024
        "%d %b %Y",  # 15 Mar 2024
        "%d-%b-%Y",  # 15-Mar-2024
        "%Y-%m-%d %H:%M:%S",  # ISO with time — drop the time
    )
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _apply_extraction(invoice: Invoice, result: ExtractionResult) -> None:
    """Apply extracted fields to the invoice record.

    Every field goes through a typed cleaner that:
    - Filters sentinel "null" / "N/A" / prompt-leak strings
    - Normalises common case + format variants
    - Drops un-parsable values silently (rather than corrupting the row)

    None of the cleaners overwrite an existing value with None — the model
    might miss a field that the human (or a previous extraction) already
    populated. We only assign when the cleaner returns a real value.
    """

    # Free-form text fields — sentinel-filter only.
    for src, dst in (
        (result.invoice_number, "invoice_number"),
        (result.vendor_name, "vendor_name"),
        (result.payment_terms, "payment_terms"),
        (result.po_number, "po_number"),
        (result.description, "description"),
        (result.vendor_address, "vendor_address"),
        (result.vendor_tax_id, "vendor_tax_id"),
        (result.reference_number, "reference_number"),
        (result.bill_to_address, "bill_to_address"),
        (result.remit_to_address, "remit_to_address"),
    ):
        cleaned = _clean_string(src.value)
        if cleaned is not None:
            setattr(invoice, dst, cleaned)

    # Decimals.
    for src, dst in (
        (result.amount, "amount"),
        (result.subtotal, "subtotal"),
        (result.tax_amount, "tax_amount"),
        (result.tax_rate, "tax_rate"),
        (result.discount_amount, "discount_amount"),
        (result.shipping_amount, "shipping_amount"),
    ):
        d = _clean_decimal(src.value)
        if d is not None:
            setattr(invoice, dst, d)

    # Dates.
    for src, dst in (
        (result.invoice_date, "invoice_date"),
        (result.due_date, "due_date"),
    ):
        d = _clean_date(src.value)
        if d is not None:
            setattr(invoice, dst, d)

    # Currency — uppercase 3-char code or None. Models sometimes return
    # "US Dollars" or "USD." with punctuation; we normalise via the
    # sentinel filter + uppercase, then validate length.
    cur = _clean_string(result.currency.value)
    if cur is not None:
        cur_upper = cur.upper().rstrip(".").strip()
        if len(cur_upper) == 3 and cur_upper.isalpha():
            invoice.currency = cur_upper

    # Payment method — map free-form to canonical dropdown values.
    pm = _normalize_payment_method(result.payment_method.value)
    if pm is not None:
        invoice.payment_method = pm
