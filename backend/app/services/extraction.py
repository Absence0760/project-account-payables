"""AI invoice extraction service.

Uses the extraction adapter pattern — supports platform (Claude Vision) and BYOK
(OpenAI, Textract, or customer's own Anthropic key) modes.

Tracks usage for billing when platform mode is used.
"""

import logging
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

logger = logging.getLogger(__name__)


def _detect_structured_format(file_bytes: bytes, file_key: str) -> str | None:
    """Return the detected structured e-invoice format value, or None.

    Pure, no network. When a file is a UBL / CII / Factur-X document we route
    it to the deterministic ``einvoice`` adapter instead of the org's
    configured vision adapter. A plain scanned PDF (or anything unstructured)
    returns None and falls through to vision/OCR unchanged.
    """
    from app.services.e_invoice import DetectedFormat, detect_format

    # filename ext (xml) helps the XML sniff when the mime is generic.
    fmt = detect_format(file_bytes, mime_type=None, filename=file_key)
    return None if fmt is DetectedFormat.NONE else fmt.value


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


def decide_auto_approve(
    ext_cfg: dict,
    approval_cfg: dict,
    *,
    overall_confidence: float,
    amount: Decimal | float | None,
) -> bool:
    """Decide whether an extracted invoice may auto-approve, skipping human review.

    Two independent triggers (either fires it):
      1. Confidence — ``auto_approve_enabled`` and ``overall_confidence`` clears
         ``auto_approve_threshold`` (default 0.95).
      2. Amount floor — ``auto_approve_below`` set and the invoice amount is
         strictly below it (the "skip review for small invoices" rule).

    But a triggered auto-approve is REVOKED when the invoice would trip the same
    money-control gates a human approval enforces
    (``services/review._enforce_approval_thresholds``):
      * ``max_invoice_amount`` — a hard reject; an over-max invoice must never
        auto-approve.
      * ``require_cfo_above`` — demands a CFO; the ``system (auto-approve)`` actor
        is not a CFO.
    When either gate would trip, the invoice falls back to human review rather
    than auto-approving past the control. Amount is compared with ``Decimal`` so
    a boundary value isn't misjudged by a float cast. Pure — no IO.
    """
    amount_dec = Decimal(str(amount or 0))

    auto_approved = False
    if ext_cfg.get("auto_approve_enabled") and overall_confidence >= ext_cfg.get(
        "auto_approve_threshold", 0.95
    ):
        auto_approved = True

    auto_below = approval_cfg.get("auto_approve_below")
    if auto_below is not None and amount_dec < Decimal(str(auto_below)):
        auto_approved = True

    if not auto_approved:
        return False

    # `cfo_gate_applies` is the shared fail-CLOSED CFO-threshold parse: a
    # malformed `require_cfo_above` counts as gate-tripped (needs_cfo=True), so a
    # settings typo revokes auto-approve into human review rather than raising an
    # InvalidOperation (which would land the invoice in `failed`) or slipping a
    # CFO-gated amount past review.
    from app.services.approval_chain import cfo_gate_applies

    max_amount = approval_cfg.get("max_invoice_amount")
    exceeds_max = max_amount is not None and amount_dec > Decimal(str(max_amount))
    needs_cfo = cfo_gate_applies(approval_cfg.get("require_cfo_above"), amount_dec)
    if exceeds_max or needs_cfo:
        return False
    return True


async def run_extraction(
    db: AsyncSession,
    invoice: Invoice,
    *,
    actor_id: uuid.UUID | None = None,
    org_settings: dict | None = None,
    ctrl_db: AsyncSession | None = None,
) -> None:
    """Extract invoice fields from the uploaded file and update the invoice.

    This is called as a background task after file upload.
    ``ctrl_db`` is the control-plane session used for ExtractionUsage tracking
    (the ``extraction_usage`` table lives in the control DB, not the tenant DB).
    """
    # Cache IDs before try block — after rollback, invoice attrs may be expired
    invoice_id = invoice.id
    invoice_org_id = invoice.organization_id
    invoice_entity_id = invoice.entity_id

    try:
        config = _resolve_extraction_config(org_settings)

        # Import adapters to trigger registration
        import app.services.extraction_adapters.aws_textract  # noqa: F401
        import app.services.extraction_adapters.claude_vision  # noqa: F401
        import app.services.extraction_adapters.einvoice_adapter  # noqa: F401
        import app.services.extraction_adapters.mock_adapter  # noqa: F401
        import app.services.extraction_adapters.ollama  # noqa: F401
        import app.services.extraction_adapters.openai_vision  # noqa: F401
        from app.services.extraction_adapters import get_extraction_adapter

        adapter = get_extraction_adapter(config)
        logger.info("[extraction] Using adapter: %s", adapter.provider_name)

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
        logger.info(
            "[extraction] Fetching file from S3: bucket=%s, key=%s",
            app_settings.s3_bucket,
            file_key,
        )
        s3_obj = s3.get_object(Bucket=app_settings.s3_bucket, Key=file_key)
        file_bytes = s3_obj["Body"].read()
        logger.info("[extraction] File fetched: %s bytes", len(file_bytes))

        # Structured e-invoice auto-detect (pure, no network). A UBL / CII /
        # Factur-X file is routed to the deterministic `einvoice` adapter,
        # overriding the org's configured vision adapter. Everything else
        # (incl. plain scanned PDFs) falls through unchanged. This is the one
        # detect site every ingress (upload + email-intake) reaches.
        structured_format = _detect_structured_format(file_bytes, file_key)
        extract_mime_type = "application/pdf"
        if structured_format is not None:
            config = {"program_type": "platform", "provider": "einvoice"}
            # CII/UBL standalone XML vs Factur-X PDF — pass the real mime so
            # the adapter's detect runs identically on the bytes.
            extract_mime_type = (
                "application/pdf" if structured_format == "facturx_pdf" else "application/xml"
            )
            logger.info("[extraction] Structured e-invoice detected: %s", structured_format)

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
            logger.info("[extraction] RAG: %s neighbors injected as few-shot", len(neighbors))

        # GL catalog: inject org-specific chart of accounts so the AI
        # uses real codes instead of the hardcoded default list.
        from sqlalchemy import or_
        from sqlalchemy import select as sa_select

        from app.models.gl_account import GLAccount

        # Scope the catalog hint to the invoice's effective chart: shared
        # accounts (entity_id NULL, available to every entity) ∪ the invoice's
        # own entity. A single-entity tenant has all accounts shared or under
        # the one entity, so this is a no-op there. See docs/multi-entity.md
        # § Chart of accounts.
        gl_result = await db.execute(
            sa_select(GLAccount)
            .where(
                GLAccount.organization_id == invoice_org_id,
                GLAccount.is_active == True,  # noqa: E712
                or_(
                    GLAccount.entity_id == invoice_entity_id,
                    GLAccount.entity_id.is_(None),
                ),
            )
            .order_by(GLAccount.code)
        )
        gl_accounts = gl_result.scalars().all()
        # Set of valid codes used post-extraction to validate AI suggestions
        # against the org's actual chart. Empty when the org hasn't synced
        # any GL accounts yet \u2014 in that mode we accept whatever the AI
        # produces (since there's nothing to validate against).
        active_gl_codes: set[str] = {gl.code for gl in gl_accounts}
        if gl_accounts:
            gl_lines = [
                f"{gl.code} \u2014 {gl.name}" + (f" [{gl.account_type}]" if gl.account_type else "")
                for gl in gl_accounts
            ]
            config["gl_account_catalog"] = "\n".join(gl_lines)
            logger.info("[extraction] GL catalog: %s accounts injected", len(gl_accounts))

        # Build a fresh adapter now that config is fully populated.
        adapter = get_extraction_adapter(config)

        result = await adapter.extract(
            file_bytes=file_bytes,
            file_key=file_key,
            mime_type=extract_mime_type,
        )

        if not result.success:
            # Don't log result.error: adapters build it from the raw provider
            # exception / response body (see extraction_adapters/*), which can
            # echo request/response content (PII). The provider name is enough
            # for triage; the structured failure lands on the exception record.
            logger.warning("[extraction] Adapter %s returned failure", adapter.provider_name)
            raise RuntimeError(result.error or "Extraction failed")

        logger.info(
            "[extraction] Success! Confidence: %s, vendor: %s",
            result.overall_confidence,
            result.vendor_name.value,
        )

        # Apply extracted fields to invoice
        _apply_extraction(invoice, result)

        # Self-correction pass — verify arithmetic, date ordering, line-item
        # math.  Lowers confidence on suspect fields and adds warnings.
        from app.services.extraction_self_correction import run_self_correction

        correction_report = await run_self_correction(result, org_settings)
        if correction_report.corrected:
            # Re-apply cleaned values after confidence adjustments
            existing_warnings = list(invoice.warnings or [])
            for v in correction_report.violations:
                existing_warnings.append(
                    {
                        "type": "extraction_self_correction",
                        "severity": v["severity"],
                        "message": v["message"],
                        "check": v["check"],
                    }
                )
            invoice.warnings = existing_warnings
            logger.info(
                "[extraction] Self-correction: %s violation(s)",
                len(correction_report.violations),
            )

        # Save line items if extracted. Drop GL codes that aren't in the
        # active chart of accounts so the invoice can still post but the
        # rejected codes don't end up in the ERP push.
        invalid_gl_codes: list[str] = []
        for li in result.line_items:
            li_gl = li.gl_account.value if li.gl_account.value else None
            if li_gl and active_gl_codes and li_gl not in active_gl_codes:
                invalid_gl_codes.append(li_gl)
                li_gl = None  # don't persist a code that doesn't exist

            line_item = InvoiceLineItem(
                invoice_id=invoice.id,
                line_number=li.line_number,
                item_code=li.item_code.value if li.item_code.value else None,
                description=li.description.value if li.description.value else None,
                quantity=_clean_decimal(li.quantity.value) if li.quantity.value else None,
                unit_price=_clean_decimal(li.unit_price.value) if li.unit_price.value else None,
                tax=_clean_decimal(li.tax.value) if li.tax.value else None,
                total=_clean_decimal(li.total.value) if li.total.value else None,
                gl_account=li_gl,
            )
            db.add(line_item)

        # Apply AI-suggested GL coding. Validate against the org's active
        # chart of accounts when one is configured — the AI is constrained
        # via the prompt but can still hallucinate, especially without
        # sufficient examples in the prompt's context window.
        suggested_gl = result.suggested_gl_account.value
        suggested_gl_conf = result.suggested_gl_account.confidence
        if suggested_gl and suggested_gl_conf >= 0.7:
            if active_gl_codes and suggested_gl not in active_gl_codes:
                invalid_gl_codes.append(suggested_gl)
            else:
                invoice.gl_account = suggested_gl

        if result.suggested_cost_center.value and result.suggested_cost_center.confidence >= 0.7:
            invoice.cost_center = result.suggested_cost_center.value

        # Surface invalid GL codes as a single aggregated warning so the
        # reviewer sees the problem in the modal and can pick the right
        # code from the dropdown.
        if invalid_gl_codes:
            existing_warnings = list(invoice.warnings or [])
            existing_warnings.append(
                {
                    "type": "gl_account_invalid",
                    "severity": "warning",
                    "message": (
                        "AI suggested GL code(s) not in active chart: "
                        + ", ".join(sorted(set(invalid_gl_codes)))
                    ),
                    "codes": sorted(set(invalid_gl_codes)),
                }
            )
            invoice.warnings = existing_warnings
            logger.info(
                "[extraction] GL validation: rejected %s unknown code(s)",
                len(set(invalid_gl_codes)),
            )

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
            # Log the field NAMES overlaid, not their values (a prior value can
            # be a sensitive field like tax_id). Mirrors audit_access field-diff
            # discipline (names, never values).
            logger.info("[extraction] Applied vendor priors: %s", sorted(applied_priors))

        # Priors can overwrite the AI-validated gl_account with a cached
        # value that was valid at the time of prior caching but has since
        # been deactivated in the chart. Re-check once after the overlay.
        if (
            "gl_account" in applied_priors
            and active_gl_codes
            and invoice.gl_account
            and invoice.gl_account not in active_gl_codes
        ):
            stale_code = invoice.gl_account
            invoice.gl_account = None
            existing_warnings = list(invoice.warnings or [])
            existing_warnings.append(
                {
                    "type": "gl_account_invalid",
                    "severity": "warning",
                    "message": (
                        f"Cached vendor GL code '{stale_code}' is no longer "
                        "in the active chart of accounts."
                    ),
                    "codes": [stale_code],
                }
            )
            invoice.warnings = existing_warnings
            logger.info("[extraction] GL validation: rejected stale prior '%s'", stale_code)

        # Semantic duplicate detection — reuses invoice_embeddings. Catches
        # near-duplicates that the rule-based (vendor_name + invoice_number)
        # check in invoice_warnings.py misses. Flags with a warning on the
        # invoice and an APException for the queue; never blocks the
        # extraction itself — reviewer decides.
        from app.services.duplicate_detection import (
            find_semantic_duplicates,
            matches_to_warning,
        )
        from app.services.exception_service import create_exception

        duplicate_matches = await find_semantic_duplicates(
            db, invoice_text, exclude_invoice_id=invoice_id
        )
        duplicate_warning = matches_to_warning(duplicate_matches)
        if duplicate_warning:
            existing = list(invoice.warnings or [])
            existing.append(duplicate_warning)
            invoice.warnings = existing
            await create_exception(
                db,
                exception_type="duplicate",
                severity="warning",
                description=duplicate_warning["message"],
                status="open",
                organization_id=invoice_org_id,
                invoice=invoice,  # exception follows its invoice (P2)
            )
            logger.info(
                "[extraction] Duplicate detection: %s near-match(es)", len(duplicate_matches)
            )

        # Refresh warnings + PO match — handles missing fields, duplicates,
        # fraud flags, vendor verification status, and 2/3-way PO matching.
        # Runs here so the reviewer sees a fully-populated invoice (with any
        # exceptions surfaced in the queue) the moment extraction lands.
        from app.services.invoice_warnings import refresh_warnings

        await refresh_warnings(db, invoice, org_settings=org_settings)

        # Save extraction result with priors metadata (what the UI will show
        # for transparency — which cache overrides and RAG neighbors shaped
        # this extraction).
        priors_metadata: dict = {}
        if applied_priors:
            priors_metadata["vendor_cache_applied"] = applied_priors
        if neighbors:
            priors_metadata["rag_neighbors"] = neighbors_to_metadata(neighbors)
        if correction_report.corrected:
            priors_metadata["self_correction"] = {
                "violations": correction_report.violations,
                "penalties": correction_report.confidence_penalties,
            }

        extraction_result = InvoiceExtractionResult(
            invoice_id=invoice.id,
            method=result.provider or config.get("provider", "unknown"),
            confidence=Decimal(str(round(result.overall_confidence, 4))),
            raw_result=result.raw_response,
            priors_metadata=priors_metadata or None,
        )
        db.add(extraction_result)

        # Track usage for billing (extraction_usage lives in the control DB)
        program_type = config.get("program_type", "platform")
        usage = ExtractionUsage(
            invoice_id=invoice.id,
            provider=result.provider or config.get("provider", "unknown"),
            program_type=program_type,
            period=datetime.now(UTC).strftime("%Y-%m"),
            success=True,
            organization_id=invoice.organization_id,
        )
        if ctrl_db is not None:
            ctrl_db.add(usage)
            await ctrl_db.commit()

        # Decide target status: auto-approve or ready_for_review
        from app.services.workflow_engine import get_step_config

        target_status = InvoiceStatus.ready_for_review
        auto_approved = False

        instance = await get_workflow_instance(db, invoice.id)
        if instance and instance.steps_config_snapshot:
            ext_cfg = get_step_config(instance.steps_config_snapshot, "extraction")
            approval_cfg = get_step_config(instance.steps_config_snapshot, "approval")
            auto_approved = decide_auto_approve(
                ext_cfg,
                approval_cfg,
                overall_confidence=result.overall_confidence,
                amount=invoice.amount,
            )
            if auto_approved:
                target_status = InvoiceStatus.approved

        if auto_approved:
            invoice.approval_date = date.today()
            invoice.approved_by = "system (auto-approve)"

        await transition_invoice(
            db,
            invoice,
            target_status,
            actor_id=actor_id,
            action_name=(
                "invoice.auto_approved" if auto_approved else "invoice.extraction_completed"
            ),
            details={
                "method": result.provider,
                "confidence": result.overall_confidence,
                "auto_approved": auto_approved,
                "vendor_action": vendor_action,
                "vendor_id": str(vendor.id) if vendor else None,
                "gl_suggested": result.suggested_gl_account.value,
                "program_type": program_type,
            },
        )

        # Advance workflow
        if instance:
            if auto_approved:
                await advance_workflow(db, instance, "erp_push", action="auto_approved")
            else:
                await advance_workflow(db, instance, "review", action="extracted")

        await db.commit()

    except Exception as exc:
        # Log the exception CLASS only, never the raw message: a vision/OCR SDK
        # exception can carry extracted invoice PII (vendor tax id / bank /
        # address). No exc_info either — this codebase installs no log-redaction
        # filter, so a traceback would put the raw message into the handler. The
        # structured failure detail lands on the exception record + audit row.
        logger.error("[extraction] Failed: %s", exc.__class__.__name__)
        await db.rollback()

        # Re-fetch invoice after rollback (the old object is expired)
        from sqlalchemy import select as sa_select

        from app.models.invoice import Invoice as InvoiceModel

        result = await db.execute(sa_select(InvoiceModel).where(InvoiceModel.id == invoice_id))
        invoice = result.scalar_one_or_none()
        if not invoice:
            await db.commit()
            return

        # Track failed usage (extraction_usage lives in the control DB)
        try:
            if ctrl_db is not None:
                config = _resolve_extraction_config(org_settings)
                usage = ExtractionUsage(
                    invoice_id=invoice_id,
                    provider=config.get("provider", "unknown"),
                    program_type=config.get("program_type", "platform"),
                    period=datetime.now(UTC).strftime("%Y-%m"),
                    success=False,
                    organization_id=invoice_org_id,
                )
                ctrl_db.add(usage)
                await ctrl_db.commit()
        except Exception:
            pass

        # Create exception record (shared chokepoint → emits `exception.raised`)
        from app.services.exception_service import create_exception

        await create_exception(
            db,
            exception_type="extraction_failed",
            severity="error",
            description=f"Extraction failed: {str(exc)[:500]}",
            status="open",
            organization_id=invoice_org_id,
            invoice=invoice,  # exception follows its invoice (P2)
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
