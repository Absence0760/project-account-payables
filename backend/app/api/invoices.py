"""Invoice CRUD endpoints."""

import csv
import io
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    ALL_ROLES,
    ROLE_ADMIN,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_current_user,
    get_org_id,
    require_roles,
)
from app.api.pagination import PaginationParams, pagination_params
from app.database import get_control_db
from app.models.contract import Contract
from app.models.entity import Entity
from app.models.exception import Exception as ExceptionModel
from app.models.invoice import Invoice, InvoiceExtractionResult, InvoiceLineItem
from app.models.invoice import InvoiceStatus as DBInvoiceStatus
from app.models.organization import Organization
from app.models.payment import Payment, PaymentSchedule
from app.models.supplier_chat import ChatAuthorRole, ChatThreadStatus, SupplierChatMessage
from app.models.user import User
from app.models.vendor import Vendor
from app.models.workflow import WorkflowInstance, WorkflowStep
from app.schemas.invoice import (
    AuditSummaryResponse,
    BulkDeleteRequest,
    BulkDeleteResponse,
    BulkExportRequest,
    BulkRecodeGLRequest,
    BulkStatusRequest,
    BulkStatusResponse,
    ChatAttachmentOut,
    ChatMessageCreate,
    ChatMessageResponse,
    ChatTemplate,
    ChatThreadResponse,
    InvoiceCountsResponse,
    InvoiceCreate,
    InvoiceListResponse,
    InvoiceResponse,
    InvoiceUpdate,
    RouteIntercompanyRequest,
)
from app.services import audit_summary
from app.services.audit_access import build_field_diff
from app.services.audit_dispatch import dispatch_audit
from app.services.csv_import import MAX_CSV_IMPORT_SIZE, import_invoices_csv
from app.services.gl_recode import RecodeFilter, bulk_recode_gl
from app.services.invoice_warnings import reconcile_line_totals, refresh_warnings
from app.services.report_export import csv_safe_cell
from app.services.storage import delete_file, get_file, upload_chat_file, upload_invoice_file
from app.services.supplier_chat import (
    CHAT_TEMPLATES,
    chat_enabled,
    get_or_create_thread,
    get_thread,
    is_valid_template_key,
    list_messages,
    notify_ap_mentions,
    notify_supplier_of_ap_message,
)
from app.services.vendor_matching import match_and_link_vendor
from app.services.workflow_engine import (
    create_workflow_instance,
    get_invoice_for_update,
    transition_invoice,
)
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant,
    get_tenant_db,
    get_write_entity_id,
)

IMMUTABLE_STATUSES = {
    DBInvoiceStatus.sending_to_erp,
    DBInvoiceStatus.sent_to_erp,
    DBInvoiceStatus.posted_in_erp,
    DBInvoiceStatus.payment_scheduled,
    DBInvoiceStatus.paid,
    DBInvoiceStatus.done,
}

# An invoice may be transmitted over PEPPOL only once it has cleared AP
# approval (mirrors the ERP-send / payment-run gate). `approved` plus every
# post-approval state; never `new` / `pending` / `ready_for_review` /
# `rejected` / `failed`.
_PEPPOL_SENDABLE_STATUSES = {DBInvoiceStatus.approved} | IMMUTABLE_STATUSES

# Once an invoice is approved, its financial content is frozen. The approval
# signature (services/approval_signature.py) is computed over the exact amount,
# and the payment run reads `Invoice.amount` straight off the row — so editing
# the amount / line items after sign-off would pay out a figure nobody approved
# and silently invalidate the signature. `approved` was the gap: IMMUTABLE_STATUSES
# only starts at `sending_to_erp`, so an edit in the `approved` window slipped
# through. Financial edits past approval must go back through reject → re-approve.
_FINANCIALLY_LOCKED_STATUSES = {DBInvoiceStatus.approved} | IMMUTABLE_STATUSES
_FINANCIAL_FIELDS = frozenset(
    {
        "amount",
        "currency",
        "subtotal",
        "tax_amount",
        "discount_amount",
        "shipping_amount",
        "tax_rate",
    }
)

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.get("", response_model=InvoiceListResponse)
async def list_invoices(
    pagination: PaginationParams = Depends(pagination_params),
    status: str | None = None,
    vendor: str | None = None,
    invoice_number: str | None = None,
    po_number: str | None = None,
    description: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    due_date_from: date | None = None,
    due_date_to: date | None = None,
    search: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    # Scope to the selected entity (None = consolidated, all entities).
    query = apply_entity_scope(select(Invoice), Invoice, entity_id)

    # Filters
    if status:
        statuses = [s.strip() for s in status.split(",")]
        query = query.where(Invoice.status.in_(statuses))
    if vendor:
        query = query.where(Invoice.vendor_name.ilike(f"%{vendor}%"))
    if invoice_number:
        query = query.where(Invoice.invoice_number.ilike(f"%{invoice_number}%"))
    if po_number:
        query = query.where(Invoice.po_number.ilike(f"%{po_number}%"))
    if description:
        query = query.where(Invoice.description.ilike(f"%{description}%"))
    if amount_min is not None:
        query = query.where(Invoice.amount >= Decimal(str(amount_min)))
    if amount_max is not None:
        query = query.where(Invoice.amount <= Decimal(str(amount_max)))
    if due_date_from:
        query = query.where(Invoice.due_date >= due_date_from)
    if due_date_to:
        query = query.where(Invoice.due_date <= due_date_to)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Invoice.vendor_name.ilike(pattern)
            | Invoice.invoice_number.ilike(pattern)
            | Invoice.po_number.ilike(pattern)
            | Invoice.description.ilike(pattern)
        )

    # Count
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    # Paginate. Eager-load extraction_results so priors_summary can be
    # computed without N+1 queries per row.
    # `created_at` is not unique (bulk/seed inserts share a timestamp), so it
    # alone gives Postgres no stable order across OFFSET/LIMIT pages — page 2
    # could re-return a page-1 row, which the frontend's keyed list rejects as a
    # duplicate id. Tie-break on the unique PK for deterministic pagination.
    query = query.order_by(Invoice.created_at.desc(), Invoice.id.desc())
    query = query.offset(pagination.offset).limit(pagination.limit)
    query = query.options(selectinload(Invoice.extraction_results))
    result = await db.execute(query)
    invoices = result.scalars().all()

    return InvoiceListResponse(
        items=[InvoiceResponse.from_db(inv) for inv in invoices],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/counts", response_model=InvoiceCountsResponse)
async def invoice_counts(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Per-status invoice tallies for the list-page filter chips.

    A single GROUP BY over the whole tenant so the "All" chip and each
    status chip stay correct regardless of how many invoices the tenant
    has — the previous client-side tally over the first page of results
    undercounted past that window. Scoped to the selected entity so the
    chips match the entity-scoped list.
    """
    counts_q = apply_entity_scope(
        select(Invoice.status, func.count()).group_by(Invoice.status), Invoice, entity_id
    )
    result = await db.execute(counts_q)
    counts: dict[str, int] = {}
    for db_status, count in result.all():
        key = db_status.value if hasattr(db_status, "value") else str(db_status)
        counts[key] = count
    return InvoiceCountsResponse(counts=counts, total=sum(counts.values()))


@router.get("/{invoice_id}", response_model=InvoiceResponse)
async def get_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    # selectinload extraction_results so InvoiceResponse.from_db ->
    # _priors_summary can read the relationship without triggering an
    # async-illegal lazy load. list_invoices already does this.
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return InvoiceResponse.from_db(invoice)


@router.get("/{invoice_id}/priors")
async def get_invoice_priors(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    """Return priors metadata from the most recent extraction.

    Shape:
        {
          "vendor_cache_applied": ["currency", "tax_rate", ...],
          "rag_neighbors": [
            {"invoice_id": "...", "similarity": 0.87, "vendor_name": "...",
             "invoice_number": "...", "amount": "..."},
          ]
        }

    Used by the invoice detail UI to show the reviewer which past corrections
    shaped the AI's output. Returns empty arrays when RAG/cache didn't fire.
    """
    result = await db.execute(
        select(InvoiceExtractionResult)
        .where(InvoiceExtractionResult.invoice_id == invoice_id)
        .order_by(InvoiceExtractionResult.created_at.desc())
        .limit(1)
    )
    row = result.scalar_one_or_none()
    metadata = (row.priors_metadata if row else None) or {}
    return {
        "vendor_cache_applied": metadata.get("vendor_cache_applied", []),
        "rag_neighbors": metadata.get("rag_neighbors", []),
    }


async def _load_invoice_for_summary(db: AsyncSession, invoice_id: uuid.UUID) -> Invoice:
    """Load an invoice with extraction_results eager-loaded (so the summary
    service can read the relationship without an async-illegal lazy load).
    404s when missing — same shape for wrong-tenant (the tenant DB simply
    won't contain the row) so the response never enumerates."""
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.get("/{invoice_id}/summary", response_model=AuditSummaryResponse)
async def get_invoice_summary(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*ALL_ROLES)),
):
    """One-paragraph natural-language summary of the invoice's audit timeline.

    Lazily generated on first open after the audit log changes; cached on
    `invoices.meta["audit_summary"]` and keyed to an audit-log fingerprint so
    it regenerates only when the timeline actually moves. Read-shaped: it may
    write the cache, but the write is fingerprint-idempotent and moves no money,
    so no idempotency key is required.
    """
    invoice = await _load_invoice_for_summary(db, invoice_id)
    return await audit_summary.get_or_build_summary(
        db, control_db, invoice, org_settings=org.settings
    )


@router.post("/{invoice_id}/summary/regenerate", response_model=AuditSummaryResponse)
async def regenerate_invoice_summary(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    control_db: AsyncSession = Depends(get_control_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Force-regenerate the audit summary, ignoring the cached fingerprint.
    Manager/admin only — backs the optional "Regenerate" button in the modal."""
    invoice = await _load_invoice_for_summary(db, invoice_id)
    return await audit_summary.get_or_build_summary(
        db, control_db, invoice, org_settings=org.settings, force=True
    )


def _buyer_identity_from_org(org: Organization, entity_name: str | None = None):
    """Build the AccountingCustomerParty (buyer = us) identity from the org's
    `settings["company"]` profile, with an optional entity-name override.

    The company address is a single string in settings → split into lines by
    the mapper. PII (tax id, address) lives inside the generated document by
    design; it never enters a log line here.
    """
    from app.services.e_invoice import BuyerIdentity

    company = (org.settings or {}).get("company") or {}
    address = company.get("address")
    address_lines = (
        [line.strip() for line in address.splitlines() if line.strip()] if address else []
    )
    return BuyerIdentity(
        name=entity_name or company.get("name") or org.name,
        tax_id=company.get("tax_id"),
        address_lines=address_lines,
        city=company.get("city"),
        postal_code=company.get("postal_code"),
        country_code=company.get("country_code"),
        email=company.get("email"),
    )


@router.get("/{invoice_id}/einvoice")
async def export_einvoice(
    invoice_id: uuid.UUID,
    format: str = "ubl",
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(*ALL_ROLES)),
):
    """Generate a standards-compliant e-invoice for an invoice.

    Maps the invoice (+ its line items + our org/entity identity as the buyer)
    into the normalized model, asserts it is valid for the requested format
    (422 on failure — an AP user must not emit a non-compliant document), then
    serializes to that format's XML.

    `format` selects the dialect: `ubl` (default, PEPPOL BIS Billing 3.0),
    `cii` (UN/CEFACT Cross-Industry Invoice — the Factur-X / ZUGFeRD dialect),
    or a registered national format — `fatturapa` (IT), `cfdi` (MX), `nfe` (BR),
    `dian` (CO). An unknown format is a 400. The national generators are
    pre-clearance documents; live government clearance (SdI / SAT-PAC / SEFAZ /
    DIAN) is a tracked follow-up — see `docs/e-invoicing.md`.
    """
    from app.models.entity import Entity
    from app.services.e_invoice import (
        EInvoiceValidationError,
        assert_valid,
        generate_cii,
        generate_ubl,
        get_country_format,
        invoice_to_einvoice_document,
    )

    # `ubl` / `cii` keep the original built-in path (shared normalized model +
    # `assert_valid` tax guard); any other token resolves a registered national
    # format (None → unsupported).
    builtin_generators = {"ubl": generate_ubl, "cii": generate_cii}
    country_format = None
    if format not in builtin_generators:
        country_format = get_country_format(format)
        if country_format is None:
            raise HTTPException(status_code=400, detail="Unsupported e-invoice format")

    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    line_items = list(
        (
            await db.execute(
                select(InvoiceLineItem)
                .where(InvoiceLineItem.invoice_id == invoice_id)
                .order_by(InvoiceLineItem.line_number)
            )
        )
        .scalars()
        .all()
    )

    entity_name: str | None = None
    if invoice.entity_id is not None:
        entity = (
            await db.execute(select(Entity).where(Entity.id == invoice.entity_id))
        ).scalar_one_or_none()
        if entity is not None:
            entity_name = entity.name

    buyer = _buyer_identity_from_org(org, entity_name=entity_name)
    doc = invoice_to_einvoice_document(invoice, line_items, buyer)

    base = invoice.invoice_number or invoice.id
    if country_format is None:
        # Built-in UBL / CII path — shared tax guard, format-specific generator.
        try:
            assert_valid(doc)
        except EInvoiceValidationError as exc:
            # str(exc) is a PII-free "field: code" join — safe in the error body.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        xml_bytes = builtin_generators[format](doc)
        media_type = "application/xml"
        tag = "" if format == "ubl" else f"-{format}"
        filename = f"einvoice-{base}{tag}.xml"
    else:
        # National format path — validate via the format, then generate.
        errors = country_format.validate(doc)
        if errors:
            # EInvoiceValidationError renders a PII-free "field: code" join.
            raise HTTPException(status_code=422, detail=str(EInvoiceValidationError(errors)))
        xml_bytes = country_format.generate(doc)
        media_type = country_format.media_type
        filename = f"einvoice-{base}-{country_format.format_code}.{country_format.file_extension}"

    return Response(
        content=xml_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class PeppolSendRequest(BaseModel):
    receiver_scheme: str  # EAS code, e.g. "9930"
    receiver_value: str  # receiver registered id
    sender_scheme: str | None = None  # falls back to org settings.peppol.sender_scheme
    sender_value: str | None = None  # falls back to org settings.peppol.sender_value


class PeppolSendResponse(BaseModel):
    transmission_id: uuid.UUID
    status: str  # "sent" | "failed" | (existing status on re-send)
    message_id: str | None
    direction: str  # "outbound"
    already_sent: bool  # True when the idempotency short-circuit hit


@router.post("/{invoice_id}/peppol-send", response_model=PeppolSendResponse)
async def peppol_send(
    invoice_id: uuid.UUID,
    body: PeppolSendRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Transmit an invoice over the PEPPOL network via the configured Access Point.

    Reuses the e-invoice pipeline (map → tax-validate → UBL) then hands the UBL
    to the PEPPOL adapter (mock default; ``as4_gateway`` when configured).
    Idempotent: a second call for the same invoice returns the existing
    transmission with ``already_sent=True`` and does not re-transmit (enforced
    at the data layer by a partial unique index).

    422 on a tax-invalid invoice or an unregistered receiver; 400 on a malformed
    participant id; 404 if the invoice is missing. PII-free error bodies.
    """
    from app.models.entity import Entity
    from app.services.e_invoice import EInvoiceValidationError
    from app.services.peppol_adapters import ParticipantId, PeppolSendError
    from app.services.peppol_send import send_invoice_over_peppol

    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    # PEPPOL transmission is a compliance-significant outbound to a counterparty
    # (inserts a row, emits to the network, writes an audit row) — gate it on AP
    # approval, exactly like the ERP-send / payment-run paths. A new / rejected /
    # failed invoice must not be transmittable.
    if invoice.status not in _PEPPOL_SENDABLE_STATUSES:
        raise HTTPException(status_code=422, detail="invoice_not_approved")

    line_items = list(
        (
            await db.execute(
                select(InvoiceLineItem)
                .where(InvoiceLineItem.invoice_id == invoice_id)
                .order_by(InvoiceLineItem.line_number)
            )
        )
        .scalars()
        .all()
    )

    entity_name: str | None = None
    if invoice.entity_id is not None:
        entity = (
            await db.execute(select(Entity).where(Entity.id == invoice.entity_id))
        ).scalar_one_or_none()
        if entity is not None:
            entity_name = entity.name

    buyer = _buyer_identity_from_org(org, entity_name=entity_name)

    peppol_config = (org.settings or {}).get("peppol") or {}
    sender_scheme = body.sender_scheme or peppol_config.get("sender_scheme")
    sender_value = body.sender_value or peppol_config.get("sender_value")
    if not sender_scheme or not sender_value:
        raise HTTPException(status_code=400, detail="sender participant id is not configured")

    try:
        sender_id = ParticipantId(scheme=str(sender_scheme), value=str(sender_value))
        # Round-trip through parse to validate the scheme format (PII-free error).
        ParticipantId.parse(sender_id.format())
        receiver_id = ParticipantId.parse(f"{body.receiver_scheme}:{body.receiver_value}")
    except ValueError as exc:
        # str(exc) names the field only — never the value (PII-free).
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        transmission, already_sent = await send_invoice_over_peppol(
            db,
            invoice=invoice,
            line_items=line_items,
            buyer=buyer,
            sender_id=sender_id,
            receiver_id=receiver_id,
            organization_id=invoice.organization_id,
            entity_id=invoice.entity_id,
            actor_id=user.id,
            peppol_config=peppol_config,
        )
    except EInvoiceValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PeppolSendError as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc

    return PeppolSendResponse(
        transmission_id=transmission.id,
        status=transmission.status,
        message_id=transmission.message_id,
        direction=transmission.direction,
        already_sent=already_sent,
    )


@router.get("/{invoice_id}/line-items")
async def get_invoice_line_items(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    from app.models.invoice import InvoiceLineItem

    result = await db.execute(
        select(InvoiceLineItem)
        .where(InvoiceLineItem.invoice_id == invoice_id)
        .order_by(InvoiceLineItem.line_number)
    )
    items = result.scalars().all()
    return [
        {
            "id": str(li.id),
            "line_number": li.line_number,
            "item_code": li.item_code,
            "description": li.description,
            "quantity": float(li.quantity) if li.quantity else None,
            "unit_price": float(li.unit_price) if li.unit_price else None,
            "tax": float(li.tax) if li.tax else None,
            "total": float(li.total) if li.total else None,
            "gl_account": li.gl_account,
        }
        for li in items
    ]


class _LineItemInput(BaseModel):
    """Validated shape for a single line item posted to PUT /{id}/line-items.

    The handler used to accept ``list[dict]``, which let any payload
    through and threw a 500 on shape mismatches (with a full traceback
    in debug mode). Defining the shape moves shape errors to a clean 422.
    """

    line_number: int | None = None
    item_code: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    gl_account: str | None = None


# Column order shared by the before/after line-item snapshots the line-items PUT
# diffs. Keep the DB SELECT and the in-memory tuple build in this exact order.
_LINE_SNAPSHOT_FIELDS = (
    "line_number",
    "item_code",
    "description",
    "quantity",
    "unit_price",
    "tax",
    "total",
    "gl_account",
)
_LINE_TOTAL_IDX = _LINE_SNAPSHOT_FIELDS.index("total")
_LINE_GL_IDX = _LINE_SNAPSHOT_FIELDS.index("gl_account")


def _canonical_lines(rows) -> list[tuple[str, ...]]:
    """Normalize line-item snapshot rows into sorted, comparable string tuples.

    The two sides come from different places — one from Postgres (`Numeric`
    scale applied: `Decimal("1.0000")`), one straight off the request body
    (`Decimal("1")`) — so a raw tuple comparison would report a change that
    didn't happen. Decimals are compared by VALUE via `normalize()`; NULL gets a
    sentinel so it can't collide with an empty string. Stringifying also makes
    the sort total (raw tuples mixing `None` and `str` raise `TypeError`).
    """

    def _val(v) -> str:
        if v is None:
            return "\x00"
        if isinstance(v, Decimal):
            return format(v.normalize(), "f")
        return str(v)

    return sorted(tuple(_val(v) for v in row) for row in rows)


@router.put("/{invoice_id}/line-items")
async def save_invoice_line_items(
    invoice_id: uuid.UUID,
    body: list[_LineItemInput],
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Replace all line items for an invoice.

    Line items are financial content, so this carries the same discipline as the
    header ``PATCH``: an append-only audit row, a re-derivation of the invoice's
    warnings, and an explicit reconciliation of the summed lines against the
    header money fields.

    The header ``amount`` is deliberately **not** recomputed from the lines — see
    ``invoice_warnings.reconcile_line_totals`` for why (line ``total`` is
    tax-inclusive on one ingest path and tax-exclusive on another, and lines are
    often partial, so an overwrite would move money with no approval behind it).
    A sum that reconciles under neither convention raises an ``error`` warning
    plus a ``line_total_mismatch`` exception, so the divergence reaches a human
    before the invoice can be approved and paid. The response reports the
    outcome so the editor sees it immediately.
    """
    from app.models.invoice import InvoiceLineItem

    # Row-lock the invoice: the delete-and-reinsert below is not atomic on its
    # own, and the status guard right after must not be read from a stale row.
    invoice = await get_invoice_for_update(db, invoice_id)
    # Line items are financial content — frozen once the invoice is approved
    # (the approved amount was signed off; payment reads it). Re-coding lines
    # after sign-off requires reject → re-approve. See _FINANCIALLY_LOCKED_STATUSES.
    if invoice.status in _FINANCIALLY_LOCKED_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="Cannot edit line items once the invoice is approved",
        )

    # Snapshot the outgoing lines. The audit row reports counts + exact Decimal
    # totals + GL codes (never a float; never the free-form line text), but the
    # change DETECTION compares every column — a re-code that swaps a GL account
    # without moving the count or the total is still a change the trail must
    # record.
    before_rows = [
        tuple(r)
        for r in (
            await db.execute(
                select(*(getattr(InvoiceLineItem, f) for f in _LINE_SNAPSHOT_FIELDS)).where(
                    InvoiceLineItem.invoice_id == invoice_id
                )
            )
        ).all()
    ]
    before_totals = [r[_LINE_TOTAL_IDX] for r in before_rows if r[_LINE_TOTAL_IDX] is not None]
    before = {
        "line_item_count": len(before_rows),
        "line_items_total": sum(before_totals, Decimal("0")) if before_totals else None,
        "gl_accounts": sorted({r[_LINE_GL_IDX] for r in before_rows if r[_LINE_GL_IDX]}),
    }

    # Delete existing line items
    await db.execute(sa_delete(InvoiceLineItem).where(InvoiceLineItem.invoice_id == invoice_id))

    # Insert new line items
    new_total: Decimal | None = None
    after_rows: list[tuple] = []
    for i, item in enumerate(body):
        line_number = item.line_number if item.line_number is not None else i + 1
        li = InvoiceLineItem(
            invoice_id=invoice_id,
            line_number=line_number,
            item_code=item.item_code,
            description=item.description,
            quantity=item.quantity,
            unit_price=item.unit_price,
            tax=item.tax,
            total=item.total,
            gl_account=item.gl_account,
        )
        db.add(li)
        after_rows.append(
            (
                line_number,
                item.item_code,
                item.description,
                item.quantity,
                item.unit_price,
                item.tax,
                item.total,
                item.gl_account,
            )
        )
        if item.total is not None:
            new_total = item.total if new_total is None else new_total + item.total
    await db.flush()

    after = {
        "line_item_count": len(body),
        "line_items_total": new_total,
        "gl_accounts": sorted({r[_LINE_GL_IDX] for r in after_rows if r[_LINE_GL_IDX]}),
    }

    # Re-derive every warning this invoice owns. The lines feed PO-match
    # variance, price variance and the new line-total reconciliation, all of
    # which were computed against the PREVIOUS lines until now.
    await refresh_warnings(db, invoice, org_settings=org.settings)
    mismatch = reconcile_line_totals(invoice, new_total) if new_total is not None else None

    # SOX change history: the lines moved, so the trail must say so. PII-free —
    # counts, exact string-Decimal money and GL codes only (the same shape
    # `bulk_recode_gl` records). Written whenever ANY column moved, even when
    # the count and the total are unchanged.
    if _canonical_lines(before_rows) != _canonical_lines(after_rows):
        field_diff = build_field_diff(
            before, after, ["line_item_count", "line_items_total", "gl_accounts"]
        )
        await dispatch_audit(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=invoice.organization_id,
            actor_id=user.id,
            action="invoice.line_items_edited",
            entity_type="invoice",
            entity_id=invoice.id,
            details={
                "changes": field_diff,
                "header_amount": str(invoice.amount),
                "reconciles_with_header": mismatch is None,
            },
        )

    await db.commit()
    return {
        "saved": len(body),
        # Exact decimal strings — never float (project invariant: money is exact).
        "line_items_total": str(new_total) if new_total is not None else None,
        "header_amount": str(invoice.amount),
        "reconciles_with_header": mismatch is None,
    }


@router.post("", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    body: InvoiceCreate,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    invoice = Invoice(
        organization_id=org_id,
        entity_id=entity_id,
        invoice_number=body.invoice_number,
        vendor_name=body.vendor,
        description=body.description,
        amount=body.amount,
        currency=body.currency,
        invoice_date=body.invoice_date,
        received_date=body.received_date,
        due_date=body.due_date,
        payment_terms=body.payment_terms,
        # Always enter the workflow at `new`; status is not caller-settable on
        # create (see InvoiceCreate). Reaching any later state goes through the
        # transition endpoints + state machine, never a create payload.
        status=DBInvoiceStatus.new,
        po_number=body.po_number,
        subtotal=body.subtotal,
        tax_amount=body.tax_amount,
        discount_amount=body.discount_amount,
        shipping_amount=body.shipping_amount,
        remit_to_address=body.remit_to_address,
        bill_to_address=body.bill_to_address,
        vendor_address=body.vendor_address,
        vendor_tax_id=body.vendor_tax_id,
        ship_to_address=body.ship_to_address,
        tax_rate=body.tax_rate,
        payment_method=body.payment_method,
        reference_number=body.reference_number,
        notes=body.notes,
        gl_account=body.gl_account,
        cost_center=body.cost_center,
        department=body.department,
        project=body.project,
    )
    db.add(invoice)
    await db.flush()
    # Resolve the vendor LINK, not just the typed-in name. Manual entry runs no
    # extraction, so nothing else would ever populate `vendor_id` — and an
    # invoice with a NULL `vendor_id` is an invoice whose vendor cannot be
    # proven, which is exactly what the credit-memo vendor guard needs to
    # compare against (see app/api/credit_memos.py). Same matcher the extraction
    # pipeline uses, so a manual entry and an extracted one land on the same
    # vendor row; an unmatched name creates an `unverified` vendor stamped
    # `source="manual"` for an AP steward to confirm.
    await match_and_link_vendor(db, invoice, org_id, source="manual")
    # Snapshot the active workflow definition onto this invoice so any
    # later config edits don't retroactively change its routing.
    await create_workflow_instance(db, invoice)
    # Re-fetch with extraction_results eager-loaded so InvoiceResponse.from_db
    # → _priors_summary doesn't trigger an async-illegal lazy load.
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice.id)
    )
    invoice = result.scalar_one()
    return InvoiceResponse.from_db(invoice)


@router.post(
    "/{invoice_id}/file", response_model=InvoiceResponse, status_code=status.HTTP_201_CREATED
)
async def attach_invoice_file(
    invoice_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Attach a source file to a manually-entered invoice that has none yet.

    Manual entry (``create_invoice``) never runs extraction, so a caller who
    wants to keep the source document alongside a manually-keyed invoice
    attaches it as a second step. Attach-only: refuses once the invoice
    already has a file rather than silently replacing it — replacing an
    existing invoice file is a separate, not-yet-built feature with its own
    role/status gating still to be decided (see docs/roadmap.md § Invoice PDF
    Management).
    """
    invoice = await _load_invoice_or_404(db, invoice_id)
    if invoice.file_key:
        raise HTTPException(status_code=409, detail="Invoice already has a file attached.")

    try:
        file_key, file_url = await upload_invoice_file(org_id, invoice.id, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    invoice.file_key = file_key
    invoice.file_url = file_url
    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=user.id,
        action="invoice.file_attached",
        entity_type="invoice",
        entity_id=invoice.id,
        details={"filename": file.filename, "content_type": file.content_type},
    )
    await db.flush()
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice.id)
    )
    invoice = result.scalar_one()
    return InvoiceResponse.from_db(invoice)


@router.put("/{invoice_id}/file", response_model=InvoiceResponse, status_code=status.HTTP_200_OK)
async def replace_invoice_file(
    invoice_id: uuid.UUID,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Replace an invoice's existing file — the companion to `attach_invoice_file`.

    `attach_invoice_file` only works when the invoice has no file yet; this
    endpoint is the reverse — it requires one to already be present (404
    otherwise, pointing the caller at attach instead) and swaps it for a new
    upload. Refused once the invoice is `done` (terminal, financially frozen)
    to match the rest of the file-mutation gating in this file.
    """
    invoice = await _load_invoice_or_404(db, invoice_id)
    if not invoice.file_key:
        raise HTTPException(status_code=404, detail="No file to replace. Use upload to attach one.")
    if invoice.status == DBInvoiceStatus.done:
        raise HTTPException(
            status_code=409, detail="Cannot modify the file once the invoice is done."
        )

    old_file_key = invoice.file_key
    try:
        file_key, file_url = await upload_invoice_file(org_id, invoice.id, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    invoice.file_key = file_key
    invoice.file_url = file_url
    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=user.id,
        action="invoice.file_replaced",
        entity_type="invoice",
        entity_id=invoice.id,
        details={
            "previous_filename": old_file_key.rsplit("/", 1)[-1],
            "filename": file_key.rsplit("/", 1)[-1],
            "content_type": file.content_type,
        },
    )
    # Commit before touching storage — the row pointing at the new key must be
    # durable before the old object is removed, so a failure here never leaves
    # `file_key` referencing an object we already deleted (mirrors the
    # commit-then-delete order in api/positive_pay.py's delete route).
    await db.commit()
    # upload_invoice_file's S3 key is deterministic on the sanitized filename
    # (no uniquifier) — replacing a file with one of the SAME name overwrites
    # the old object in place, so old_file_key == file_key. Deleting it in
    # that case would delete the file we just wrote.
    if old_file_key != file_key:
        delete_file(old_file_key)
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice.id)
    )
    invoice = result.scalar_one()
    return InvoiceResponse.from_db(invoice)


@router.delete("/{invoice_id}/file", response_model=InvoiceResponse, status_code=status.HTTP_200_OK)
async def delete_invoice_file(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Delete an invoice's file without replacing it."""
    invoice = await _load_invoice_or_404(db, invoice_id)
    if not invoice.file_key:
        raise HTTPException(status_code=404, detail="No file to delete.")
    if invoice.status == DBInvoiceStatus.done:
        raise HTTPException(
            status_code=409, detail="Cannot modify the file once the invoice is done."
        )

    filename = invoice.file_key.rsplit("/", 1)[-1]
    file_key_to_delete = invoice.file_key
    invoice.file_key = None
    invoice.file_url = None
    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=user.id,
        action="invoice.file_deleted",
        entity_type="invoice",
        entity_id=invoice.id,
        details={"filename": filename},
    )
    # Commit before touching storage — see replace_invoice_file for why
    # (mirrors api/positive_pay.py's commit-then-delete order).
    await db.commit()
    delete_file(file_key_to_delete)
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice.id)
    )
    invoice = result.scalar_one()
    return InvoiceResponse.from_db(invoice)


@router.patch("/{invoice_id}", response_model=InvoiceResponse)
async def update_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    # selectinload extraction_results — see get_invoice for the why.
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in IMMUTABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot update invoice in this status")

    update_data = body.model_dump(exclude_unset=True)
    # An approved invoice is financially frozen — the signed-off amount is what
    # the payment run pays. Non-financial edits (notes, addresses, GL coding)
    # stay allowed in the `approved` window so AP can keep cleaning up metadata;
    # touching a money field requires reject → re-approve. (Past ERP-send the
    # IMMUTABLE_STATUSES check above has already blocked the whole edit.)
    if invoice.status in _FINANCIALLY_LOCKED_STATUSES:
        touched_financial = _FINANCIAL_FIELDS & set(update_data)
        if touched_financial:
            raise HTTPException(
                status_code=409,
                detail="Cannot edit financial fields once the invoice is approved",
            )
    # Map frontend field name to DB column
    if "vendor" in update_data:
        update_data["vendor_name"] = update_data.pop("vendor")
    # `status` is intentionally NOT an editable field here (it was removed from
    # InvoiceUpdate). A status change is a workflow transition — it must run
    # through validate_transition + segregation + thresholds + the CFO gate +
    # the approval signature + the immutable audit row, all of which live on the
    # dedicated transition endpoints (services.review / workflow_engine). A bare
    # setattr here would bypass every one of them. Defensively drop it in case a
    # caller smuggles it in (Pydantic already strips it from update_data, so this
    # is belt-and-suspenders against a future schema edit re-adding the field).
    update_data.pop("status", None)

    # Capture a per-field before/after diff for the audit trail (SOX change
    # history). Money fields serialise as string-Decimal inside the diff.
    before = {field: getattr(invoice, field, None) for field in update_data}
    for field, value in update_data.items():
        setattr(invoice, field, value)

    # Re-resolve the vendor LINK whenever the vendor name is (re)saved and the
    # link is stale or absent. `vendor_id` — not `vendor_name` — is what the
    # credit-memo vendor guard compares against, so a rename that left the old
    # link in place would let vendor A's credit apply to what is now vendor B's
    # invoice, and an invoice that never got a link at all (manual entry keyed
    # in before create_invoice resolved one) could never be credited. Re-saving
    # the vendor name is therefore also the supported, audited way to resolve a
    # legacy unlinked invoice — no backfill migration guesses at it.
    #
    # Clearing the name clears the LINK too. `match_and_link_vendor` no-ops on a
    # blank name, which would otherwise leave a nameless invoice still pointing
    # at the previous vendor — a link nothing visible corroborates, yet one the
    # credit guard would happily accept. Blank name → no provable vendor → NULL,
    # i.e. un-creditable until someone names the vendor again.
    diff_fields = list(update_data.keys())
    if "vendor_name" in update_data and (
        before.get("vendor_name") != invoice.vendor_name or invoice.vendor_id is None
    ):
        before["vendor_id"] = invoice.vendor_id
        if (invoice.vendor_name or "").strip():
            await match_and_link_vendor(db, invoice, org.id, source="manual")
        else:
            invoice.vendor_id = None
        diff_fields.append("vendor_id")

    after = {field: getattr(invoice, field, None) for field in diff_fields}

    field_diff = build_field_diff(before, after, diff_fields)
    if field_diff:
        await dispatch_audit(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=invoice.organization_id,
            actor_id=user.id,
            action="invoice.edited",
            entity_type="invoice",
            entity_id=invoice.id,
            details={"changes": field_diff},
        )

    await refresh_warnings(db, invoice, org_settings=org.settings)
    await db.flush()
    # The in-memory `invoice` already reflects setattr + refresh_warnings,
    # and selectinload(extraction_results) was applied on the initial fetch
    # at the top of this function. Build the response directly from it —
    # re-selecting hits the session's identity map and could surface stale
    # state in some pool/connection interleavings.
    return InvoiceResponse.from_db(invoice)


class LinkContractRequest(BaseModel):
    contract_id: str


@router.post("/{invoice_id}/link-contract", response_model=InvoiceResponse)
async def link_contract(
    invoice_id: uuid.UUID,
    body: LinkContractRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Attribute this invoice's spend to a contract.

    Linking is allowed in any invoice status (spend attribution on a paid
    invoice is exactly when you want it). Re-running ``refresh_warnings``
    recomputes the contract-compliance flags for the new link.
    """
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    try:
        contract_uuid = uuid.UUID(body.contract_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid contract_id")
    contract = (
        await db.execute(select(Contract).where(Contract.id == contract_uuid))
    ).scalar_one_or_none()
    if not contract:
        raise HTTPException(status_code=404, detail="Contract not found")

    if invoice.contract_id != contract_uuid:
        invoice.contract_id = contract_uuid
        await dispatch_audit(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=invoice.organization_id,
            actor_id=user.id,
            action="invoice.contract_linked",
            entity_type="invoice",
            entity_id=invoice.id,
            details={
                "contract_id": str(contract_uuid),
                "contract_number": contract.contract_number,
            },
        )
        await refresh_warnings(db, invoice, org_settings=org.settings)
    await db.flush()
    return InvoiceResponse.from_db(invoice)


@router.post("/{invoice_id}/unlink-contract", response_model=InvoiceResponse)
async def unlink_contract(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    result = await db.execute(
        select(Invoice)
        .options(selectinload(Invoice.extraction_results))
        .where(Invoice.id == invoice_id)
    )
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")

    if invoice.contract_id is not None:
        invoice.contract_id = None
        await dispatch_audit(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=invoice.organization_id,
            actor_id=user.id,
            action="invoice.contract_unlinked",
            entity_type="invoice",
            entity_id=invoice.id,
            details=None,
        )
        await refresh_warnings(db, invoice, org_settings=org.settings)
    await db.flush()
    return InvoiceResponse.from_db(invoice)


@router.post("/{invoice_id}/route-intercompany", response_model=InvoiceResponse)
async def route_intercompany(
    invoice_id: uuid.UUID,
    body: RouteIntercompanyRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Generate the mirror payable for an inter-company charge (multi-entity).

    Sets the named counterparty entity on this invoice, then routes it: a mirror
    payable Invoice is created under the counterparty entity, linked back to this
    one. Idempotent at the boundary — calling twice returns the same mirror and
    never creates a second. Idempotency is enforced at *three* layers, because a
    duplicate live payable is a double liability:

      1. the origin row is taken ``FOR UPDATE`` here, so two concurrent callers
         serialize and the loser re-reads the (now-set) ``intercompany_mirror_id``;
      2. the routing service short-circuits on that column;
      3. the partial unique index ``uq_invoice_intercompany_mirror`` makes a
         second mirror impossible to persist even if a future caller bypasses
         the lock — the losing INSERT surfaces here as a clean 409.

    Returns the mirror invoice.
    """
    from app.services.intercompany import route_intercompany_invoice

    # Row-lock the origin BEFORE the dedupe check. Without the lock two
    # concurrent calls both observed `intercompany_mirror_id IS NULL` and each
    # created a live mirror payable under the counterparty entity — the orphan
    # could then be approved and paid on its own. `get_invoice_for_update`
    # eager-loads extraction_results, same as the plain select did.
    invoice = await get_invoice_for_update(db, invoice_id)

    try:
        counterparty_uuid = uuid.UUID(body.counterparty_entity_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid counterparty_entity_id")

    # Validate the counterparty is a real entity in THIS tenant (the entities
    # table is tenant-local, so an unknown id can't point at another tenant's
    # subsidiary — same guard `app.tenant.get_entity_id` uses).
    exists = (
        await db.execute(select(Entity.id).where(Entity.id == counterparty_uuid))
    ).scalar_one_or_none()
    if exists is None:
        raise HTTPException(status_code=404, detail="Unknown counterparty entity for this tenant")

    # Only stamp the counterparty when this invoice has NOT been routed yet.
    # Once the mirror exists the pairing is settled: re-pointing the origin at a
    # different entity here would leave it claiming a counterparty its mirror
    # doesn't sit under, with no second mirror ever generated. Re-routing is a
    # reject-and-re-route operation, not a silent field edit.
    if invoice.intercompany_mirror_id is None:
        invoice.counterparty_entity_id = counterparty_uuid
    try:
        mirror = await route_intercompany_invoice(db, invoice, actor_id=user.id)
    except ValueError as exc:
        # Self-billing or a missing counterparty — a client error, PII-free.
        raise HTTPException(status_code=400, detail=str(exc))
    except IntegrityError:
        # The uq_invoice_intercompany_mirror backstop fired: another writer
        # already generated this origin's mirror. Nothing was persisted — roll
        # back and report the conflict. PII-free.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This invoice has already been routed inter-company",
        )
    await db.flush()

    # Re-fetch the mirror with extraction_results eager-loaded so
    # InvoiceResponse.from_db → _priors_summary doesn't trigger a lazy load.
    mirror_row = (
        await db.execute(
            select(Invoice)
            .options(selectinload(Invoice.extraction_results))
            .where(Invoice.id == mirror.id)
        )
    ).scalar_one()
    return InvoiceResponse.from_db(mirror_row)


# ---------------------------------------------------------------------------
# Supplier chat (AP side). Service logic lives in services/supplier_chat.py;
# these handlers own the HTTP shape + RBAC. Datetimes serialize as ISO strings
# (invoice.py convention). See backend/docs/supplier-chat.md.
# ---------------------------------------------------------------------------


def _chat_message_to_response(msg: SupplierChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=str(msg.id),
        thread_id=str(msg.thread_id),
        author_role=str(msg.author_role),
        author_user_id=str(msg.author_user_id) if msg.author_user_id else None,
        author_name=msg.author_name,
        body=msg.body,
        mention_user_ids=[str(m) for m in (msg.mentions or [])],
        template_key=msg.template_key,
        attachments=[ChatAttachmentOut(**a) for a in (msg.attachments or [])],
        created_at=msg.created_at.isoformat() if msg.created_at else "",
    )


async def _chat_thread_response(db: AsyncSession, invoice: Invoice) -> ChatThreadResponse:
    thread = await get_thread(db, invoice.id)
    if thread is None:
        return ChatThreadResponse(
            id=None,
            invoice_id=str(invoice.id),
            status=ChatThreadStatus.open.value,
            messages=[],
        )
    messages = await list_messages(db, thread.id)
    return ChatThreadResponse(
        id=str(thread.id),
        invoice_id=str(invoice.id),
        status=str(thread.status),
        resolved_at=thread.resolved_at.isoformat() if thread.resolved_at else None,
        resolved_by=str(thread.resolved_by) if thread.resolved_by else None,
        messages=[_chat_message_to_response(m) for m in messages],
    )


async def _load_invoice_or_404(db: AsyncSession, invoice_id: uuid.UUID) -> Invoice:
    invoice = (
        await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    ).scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.get("/chat/templates", response_model=list[ChatTemplate])
async def get_chat_templates(
    user: User = Depends(get_current_user),
):
    """Static, in-code canned templates (the source of truth)."""
    return [ChatTemplate(**t) for t in CHAT_TEMPLATES]


@router.get("/chat/file/{file_key:path}")
async def get_chat_file(
    file_key: str,
    user: User = Depends(get_current_user),
):
    """Proxy a stored chat attachment from S3.

    Keys are stamped ``<org_id>/chat/<invoice_id>/<message_id>/<filename>``. The
    caller must belong to the org in the first segment — same 404 for wrong-org
    and missing-file so the response can't enumerate prefixes.
    """
    prefix = file_key.split("/", 1)[0]
    if prefix != str(user.organization_id):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content, content_type = get_file(file_key)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(content=content, media_type=content_type)


@router.get("/{invoice_id}/chat", response_model=ChatThreadResponse)
async def get_invoice_chat(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
):
    invoice = await _load_invoice_or_404(db, invoice_id)
    # Feature off → empty thread (never lazy-create on a read either way).
    if not chat_enabled(org):
        return ChatThreadResponse(
            id=None,
            invoice_id=str(invoice.id),
            status=ChatThreadStatus.open.value,
            messages=[],
        )
    return await _chat_thread_response(db, invoice)


async def _post_ap_chat_message(
    db: AsyncSession,
    org: Organization,
    user: User,
    invoice: Invoice,
    *,
    body: str,
    mention_user_ids: list[str],
    template_key: str | None,
    attachments: list[dict] | None,
) -> ChatMessageResponse:
    """Shared core for the JSON and multipart AP message POSTs."""
    if not is_valid_template_key(template_key):
        raise HTTPException(status_code=400, detail="Unknown template_key")

    parsed_mentions: list[uuid.UUID] = []
    for raw in mention_user_ids:
        try:
            parsed_mentions.append(uuid.UUID(raw))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid mention_user_ids")

    thread = await get_or_create_thread(db, invoice)
    msg = SupplierChatMessage(
        thread_id=thread.id,
        author_role=ChatAuthorRole.ap_team,
        author_user_id=user.id,
        author_name=user.full_name,
        body=body,
        mentions=[str(m) for m in parsed_mentions] or None,
        attachments=attachments or None,
        template_key=template_key,
    )
    db.add(msg)
    await db.flush()

    # Audit (PII-free: ids/roles/booleans only — no body/email/filename).
    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=user.id,
        action="chat_message_posted",
        entity_type="invoice",
        entity_id=invoice.id,
        details={
            "thread_id": str(thread.id),
            "message_id": str(msg.id),
            "author_role": str(msg.author_role),
            "has_attachment": bool(msg.attachments),
            "template_key": msg.template_key,
        },
    )

    # In-app notifications to mentioned teammates (gated by notifications_enabled
    # inside notify_event); adds rows onto this same tenant txn → call before commit.
    await notify_ap_mentions(
        db,
        invoice=invoice,
        mention_user_ids=parsed_mentions,
        actor_id=user.id,
    )

    # Direct supplier email (best-effort, PII-free, gated internally).
    vendor = None
    if invoice.vendor_id:
        vendor = (
            await db.execute(select(Vendor).where(Vendor.id == invoice.vendor_id))
        ).scalar_one_or_none()
    await notify_supplier_of_ap_message(db, org=org, invoice=invoice, vendor=vendor)

    await db.commit()
    await db.refresh(msg)
    return _chat_message_to_response(msg)


@router.post(
    "/{invoice_id}/chat",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_invoice_chat(
    invoice_id: uuid.UUID,
    payload: ChatMessageCreate,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
):
    if not chat_enabled(org):
        raise HTTPException(status_code=403, detail="Supplier chat is disabled")
    invoice = await _load_invoice_or_404(db, invoice_id)
    return await _post_ap_chat_message(
        db,
        org,
        user,
        invoice,
        body=payload.body,
        mention_user_ids=payload.mention_user_ids,
        template_key=payload.template_key,
        attachments=None,
    )


@router.post(
    "/{invoice_id}/chat/attachments",
    response_model=ChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_invoice_chat_attachment(
    invoice_id: uuid.UUID,
    file: UploadFile = File(...),
    body: str = Form(default=""),
    mention_user_ids: list[str] = Form(default=[]),
    template_key: str | None = Form(default=None),
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(get_current_user),
):
    if not chat_enabled(org):
        raise HTTPException(status_code=403, detail="Supplier chat is disabled")
    invoice = await _load_invoice_or_404(db, invoice_id)

    message_id = uuid.uuid4()
    try:
        file_key, filename, content_type, size = await upload_chat_file(
            org.id, invoice.id, message_id, file
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    attachment = {
        "file_key": file_key,
        "file_url": f"/api/invoices/{invoice.id}/chat/file/{file_key}",
        "filename": filename,
        "content_type": content_type,
        "size": size,
    }
    return await _post_ap_chat_message(
        db,
        org,
        user,
        invoice,
        body=body or filename,
        mention_user_ids=mention_user_ids,
        template_key=template_key,
        attachments=[attachment],
    )


@router.post("/{invoice_id}/chat/resolve", response_model=ChatThreadResponse)
async def resolve_invoice_chat(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    invoice = await _load_invoice_or_404(db, invoice_id)
    thread = await get_thread(db, invoice.id)
    if thread is None:
        raise HTTPException(status_code=404, detail="No chat thread found")
    thread.status = ChatThreadStatus.resolved
    thread.resolved_at = datetime.now(UTC)
    thread.resolved_by = user.id
    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=user.id,
        action="chat_thread_resolved",
        entity_type="invoice",
        entity_id=invoice.id,
        details={"thread_id": str(thread.id)},
    )
    await db.commit()
    return await _chat_thread_response(db, invoice)


@router.post("/{invoice_id}/chat/reopen", response_model=ChatThreadResponse)
async def reopen_invoice_chat(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    invoice = await _load_invoice_or_404(db, invoice_id)
    thread = await get_thread(db, invoice.id)
    if thread is None:
        raise HTTPException(status_code=404, detail="No chat thread found")
    thread.status = ChatThreadStatus.open
    thread.resolved_at = None
    thread.resolved_by = None
    await dispatch_audit(
        db,
        correlation_id=invoice.correlation_id,
        organization_id=invoice.organization_id,
        actor_id=user.id,
        action="chat_thread_reopened",
        entity_type="invoice",
        entity_id=invoice.id,
        details={"thread_id": str(thread.id)},
    )
    await db.commit()
    return await _chat_thread_response(db, invoice)


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    result = await db.execute(select(Invoice).where(Invoice.id == invoice_id))
    invoice = result.scalar_one_or_none()
    if not invoice:
        raise HTTPException(status_code=404, detail="Invoice not found")
    if invoice.status in IMMUTABLE_STATUSES:
        raise HTTPException(status_code=409, detail="Cannot delete invoice in this status")
    await _delete_invoice_cascade(db, invoice_id)
    await db.commit()


# ---------- Helpers ----------


async def _delete_invoice_cascade(db: AsyncSession, invoice_id: uuid.UUID) -> None:
    """Delete an invoice and all related records across tables."""
    # Delete workflow steps (child of workflow_instances)
    wf_ids_q = select(WorkflowInstance.id).where(WorkflowInstance.invoice_id == invoice_id)
    await db.execute(sa_delete(WorkflowStep).where(WorkflowStep.instance_id.in_(wf_ids_q)))
    # Delete direct children of invoices
    for model in (
        ExceptionModel,
        Payment,
        PaymentSchedule,
        WorkflowInstance,
        InvoiceExtractionResult,
        InvoiceLineItem,
    ):
        await db.execute(sa_delete(model).where(model.invoice_id == invoice_id))
    await db.execute(sa_delete(Invoice).where(Invoice.id == invoice_id))


# ---------- Bulk operations ----------


def _invoice_to_export_dict(inv: Invoice) -> dict:
    return {
        "invoice_number": inv.invoice_number,
        "vendor": inv.vendor_name,
        "amount": str(inv.amount),
        "currency": inv.currency,
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "po_number": inv.po_number,
        "description": inv.description,
        "subtotal": str(inv.subtotal) if inv.subtotal else None,
        "tax_amount": str(inv.tax_amount) if inv.tax_amount else None,
        "gl_account": inv.gl_account,
        "cost_center": inv.cost_center,
        "correlation_id": str(inv.correlation_id),
    }


@router.post("/import-csv", status_code=status.HTTP_200_OK)
async def import_invoices_from_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    """Bulk-import historical invoices from a CSV export.

    Use this to load a new tenant's open AP + historical invoices on Day 0.
    Unknown vendors are auto-created with ``status='unverified'`` so rows
    always land. Duplicate detection: ``(vendor, invoice_number)``. Imported
    rows land under the selected (or default) entity. See
    ``backend/docs/csv-import.md`` for the column list and template.
    """
    raw = await file.read()
    if len(raw) > MAX_CSV_IMPORT_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds maximum size of {MAX_CSV_IMPORT_SIZE // (1024 * 1024)} MB",
        )
    try:
        csv_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="CSV must be UTF-8 encoded") from None

    result = await import_invoices_csv(db, org_id, csv_text, entity_id=entity_id)
    await db.commit()
    return result.to_dict()


@router.post("/bulk/delete", response_model=BulkDeleteResponse)
async def bulk_delete(
    body: BulkDeleteRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    ids = [uuid.UUID(i) for i in body.ids]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(ids)))
    invoices = result.scalars().all()

    deleted = 0
    skipped: list[str] = []
    for inv in invoices:
        if inv.status in IMMUTABLE_STATUSES:
            skipped.append(str(inv.id))
        else:
            await _delete_invoice_cascade(db, inv.id)
            deleted += 1

    await db.commit()
    return BulkDeleteResponse(deleted=deleted, skipped=skipped)


@router.post("/bulk/status", response_model=BulkStatusResponse)
async def bulk_status_change(
    body: BulkStatusRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    ids = [uuid.UUID(i) for i in body.ids]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(ids)))
    invoices = result.scalars().all()

    target = DBInvoiceStatus(body.status.value)
    updated = 0
    skipped: list[str] = []
    # Bulk-approving must NOT bypass the approval controls. Routing a transition
    # straight to `approved` skipped segregation-of-duties, the max-amount cap,
    # and the CFO gate — so an AP manager could bulk-approve their own uploads or
    # push a CFO-gated invoice through. When the target is `approved`, run each
    # invoice through the same review.approve_invoice path the single-invoice
    # endpoint uses; an invoice that fails a control (403/422) is skipped, not
    # aborting the batch.
    approving = target == DBInvoiceStatus.approved
    actor_roles = {r.name for r in user.roles}
    for inv in invoices:
        if inv.status in IMMUTABLE_STATUSES:
            skipped.append(str(inv.id))
            continue
        if approving:
            from app.services.review import approve_invoice

            try:
                await approve_invoice(
                    db,
                    inv,
                    actor_id=user.id,
                    actor_name=user.full_name,
                    actor_roles=actor_roles,
                    org_settings=org.settings,
                )
            except HTTPException:
                # Segregation / threshold / CFO-gate violation — skip this one,
                # keep processing the rest of the batch.
                skipped.append(str(inv.id))
                continue
            updated += 1
        else:
            await transition_invoice(
                db,
                inv,
                target,
                actor_id=user.id,
                action_name="invoice.bulk_status_change",
            )
            await refresh_warnings(db, inv, org_settings=org.settings)
            updated += 1
    await db.commit()

    return BulkStatusResponse(updated=updated, skipped=skipped)


@router.post("/bulk/export")
async def bulk_export(
    body: BulkExportRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(get_current_user),
):
    ids = [uuid.UUID(i) for i in body.ids]
    result = await db.execute(select(Invoice).where(Invoice.id.in_(ids)))
    invoices = result.scalars().all()

    if not invoices:
        raise HTTPException(status_code=404, detail="No invoices found")

    rows = [_invoice_to_export_dict(inv) for inv in invoices]

    if body.format == "xml":
        root = ET.Element("Invoices")
        for row in rows:
            inv_el = ET.SubElement(root, "Invoice")
            for key, value in row.items():
                child = ET.SubElement(inv_el, key)
                child.text = value if value is not None else ""
        content = ET.tostring(root, encoding="unicode", xml_declaration=True)
        return Response(
            content=content,
            media_type="application/xml",
            headers={"Content-Disposition": 'attachment; filename="invoices-export.xml"'},
        )

    elif body.format == "csv":
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=rows[0].keys())
        writer.writeheader()
        # Neutralize CSV formula injection (CWE-1236) — attacker-controlled
        # fields (vendor name) flow into a CSV a CFO opens in Excel.
        writer.writerows({k: csv_safe_cell(v) for k, v in r.items()} for r in rows)
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="invoices-export.csv"'},
        )

    else:
        return rows


@router.post("/bulk-recode-gl")
async def bulk_recode_gl_endpoint(
    body: BulkRecodeGLRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN)),
    org_id: uuid.UUID = Depends(get_org_id),
    ctrl_db: AsyncSession = Depends(get_control_db),
):
    """Re-apply GL codes to a date / vendor scoped slice of invoices.

    Strategy: vendor priors first (free), then AI re-extraction for
    invoices with no usable prior (billed, opt-in via
    `include_ai_fallback`). Defaults to dry-run; the response includes
    the changes that would land plus per-source counts.

    Eligibility: invoices in immutable statuses (sending_to_erp through
    paid) are skipped — re-coding a posted invoice would create
    reconciliation drift with the ERP.
    """
    try:
        vendor_uuids = [uuid.UUID(v) for v in body.vendor_ids]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid vendor_id: {exc}") from exc

    if body.from_date and body.to_date and body.from_date > body.to_date:
        raise HTTPException(status_code=400, detail="from_date must be <= to_date")

    filt = RecodeFilter(
        from_date=body.from_date,
        to_date=body.to_date,
        vendor_ids=vendor_uuids,
    )

    report = await bulk_recode_gl(
        db,
        organization_id=org_id,
        filt=filt,
        include_ai_fallback=body.include_ai_fallback,
        dry_run=body.dry_run,
        actor_id=user.id,
        org_settings=org.settings,
        ctrl_db=ctrl_db,
    )

    return report.as_dict()
