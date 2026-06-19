"""Supplier portal — invoice submission/listing + payment history.

Every endpoint is vendor-scoped: the caller's `vendor_id` (from the JWT via
`get_current_vendor_user`) is the only vendor ID the handler ever filters on.
A vendor user cannot reference another vendor's invoices even by guessing IDs.
"""

import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pagination import PaginationParams, pagination_params
from app.api.portal_deps import get_current_vendor_user
from app.database import get_control_db
from app.models.discount import (
    OFFER_STATUS_OFFERED,
    DiscountOffer,
)
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.payment import Payment
from app.models.procurement import POLineItem, PurchaseOrder
from app.models.supplier_chat import ChatAuthorRole, ChatThreadStatus, SupplierChatMessage
from app.models.vendor import Vendor
from app.models.vendor_change_request import VendorChangeRequest
from app.models.vendor_user import VendorUser
from app.schemas.portal import (
    PortalAcceptOfferRequest,
    PortalBankChangeRequest,
    PortalChangeRequestResponse,
    PortalChatAttachmentOut,
    PortalChatMessageCreate,
    PortalChatMessageResponse,
    PortalChatThreadResponse,
    PortalCompanyInfoResponse,
    PortalCompanyInfoUpdateRequest,
    PortalDiscountOfferListResponse,
    PortalDiscountOfferResponse,
    PortalDiscountTier,
    PortalFlipResponse,
    PortalInvoiceListItem,
    PortalInvoiceListResponse,
    PortalPaymentListItem,
    PortalPaymentListResponse,
    PortalPendingChange,
    PortalPODetail,
    PortalPOLineItem,
    PortalPOListItem,
    PortalPOListResponse,
    PortalTaxIdChangeRequest,
)
from app.services import discount_offers as offers_svc
from app.services.audit_dispatch import dispatch_audit
from app.services.extraction_dispatch import dispatch_extraction
from app.services.invoice_warnings import refresh_warnings
from app.services.storage import get_file, upload_chat_file, upload_invoice_file
from app.services.supplier_chat import (
    chat_enabled,
    get_or_create_thread,
    get_thread,
    list_messages,
    notify_supplier_post,
)
from app.services.workflow_engine import (
    create_workflow_instance,
    create_workflow_step,
    is_step_enabled,
    transition_invoice,
)
from app.tenant import get_tenant_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/portal", tags=["portal"])


def _last4(value: str | None) -> str | None:
    s = (value or "").strip()
    return s[-4:] if len(s) >= 4 else None


# ---------- Invoices ----------


@router.get("/invoices", response_model=PortalInvoiceListResponse)
async def list_my_invoices(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    query = select(Invoice).where(Invoice.vendor_id == vu.vendor_id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0

    query = (
        query.order_by(Invoice.created_at.desc()).offset(pagination.offset).limit(pagination.limit)
    )
    rows = (await db.execute(query)).scalars().all()

    return PortalInvoiceListResponse(
        items=[
            PortalInvoiceListItem(
                id=str(inv.id),
                invoice_number=inv.invoice_number or "",
                amount=inv.amount,
                currency=inv.currency,
                status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
                invoice_date=inv.invoice_date,
                due_date=inv.due_date,
                submitted_at=inv.created_at,
                file_url=inv.file_url,
            )
            for inv in rows
        ],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/invoices/{invoice_id}", response_model=PortalInvoiceListItem)
async def get_my_invoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    # Ownership check is the `vendor_id ==` clause — a 404 is returned for
    # both "doesn't exist" and "belongs to another vendor" so the portal
    # can't be used to probe for invoice IDs across vendors.
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id, Invoice.vendor_id == vu.vendor_id)
    )
    inv = result.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return PortalInvoiceListItem(
        id=str(inv.id),
        invoice_number=inv.invoice_number or "",
        amount=inv.amount,
        currency=inv.currency,
        status=inv.status.value if hasattr(inv.status, "value") else str(inv.status),
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        submitted_at=inv.created_at,
        file_url=inv.file_url,
    )


@router.get("/invoices/{invoice_id}/einvoice")
async def get_my_invoice_einvoice(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctrl_db: AsyncSession = Depends(get_control_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Vendor-scoped UBL 2.1 e-invoice download for one of the supplier's own
    invoices.

    Ownership is enforced by the `Invoice.vendor_id == vu.vendor_id` clause — a
    request for another vendor's (or another tenant's) invoice returns 404, the
    same "doesn't exist / not yours" conflation the other portal routes use, so
    a vendor can never probe for or download a foreign document.

    Unlike the authenticated AP export, this does NOT 422 a supplier on a
    tax-validation soft-warning: the supplier is downloading a representation of
    an already-stored invoice, so we always return the generated UBL. Any
    validation issue is logged field-only (never to the vendor, never the value).
    """
    from app.models.organization import Organization
    from app.services.e_invoice import (
        BuyerIdentity,
        generate_ubl,
        invoice_to_einvoice_document,
        validate_document,
    )

    invoice = (
        await db.execute(
            select(Invoice).where(Invoice.id == invoice_id, Invoice.vendor_id == vu.vendor_id)
        )
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

    # The buyer (the AP customer) is an Organization in the control plane —
    # portal sessions don't carry one, so resolve it via the injected control
    # session by the invoice's org id (same pattern as get_my_payment_remittance).
    org = (
        await ctrl_db.execute(
            select(Organization).where(Organization.id == invoice.organization_id)
        )
    ).scalar_one_or_none()
    company = ((org.settings or {}).get("company") if org else None) or {}
    address = company.get("address")
    address_lines = (
        [line.strip() for line in address.splitlines() if line.strip()] if address else []
    )
    buyer = BuyerIdentity(
        name=company.get("name") or (org.name if org else "Customer"),
        tax_id=company.get("tax_id"),
        address_lines=address_lines,
        city=company.get("city"),
        postal_code=company.get("postal_code"),
        country_code=company.get("country_code"),
        email=company.get("email"),
    )

    doc = invoice_to_einvoice_document(invoice, line_items, buyer)
    # Soft-validate: log field-only codes, never block the supplier or leak values.
    errors = validate_document(doc)
    if errors:
        logger.info(
            "portal e-invoice export has validation warnings: %s",
            "; ".join(f"{e.field}: {e.code}" for e in errors),
        )

    xml_bytes = generate_ubl(doc)
    filename = f"einvoice-{invoice.invoice_number or invoice.id}.xml"
    return Response(
        content=xml_bytes,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/invoices", status_code=status.HTTP_202_ACCEPTED)
async def submit_invoice(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Supplier-side invoice submission. Routes straight into the existing
    extraction pipeline — AP sees it in the queue same as an internal upload,
    but with vendor_id / vendor_name pre-filled and a "source=supplier_portal"
    audit breadcrumb."""
    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    invoice = Invoice(
        invoice_number="",
        vendor_name=vendor.name,
        vendor_id=vendor.id,
        description="",
        amount=Decimal("0"),
        currency="USD",
        status=InvoiceStatus.new,
        organization_id=vendor.organization_id,
        # The supplier portal has no entity selector — a vendor's invoice lands
        # under the same entity as the vendor (multi-entity Phase 2).
        entity_id=vendor.entity_id,
    )
    db.add(invoice)
    await db.flush()

    try:
        file_key, file_url = await upload_invoice_file(vendor.organization_id, invoice.id, file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    invoice.file_key = file_key
    invoice.file_url = file_url

    instance = await create_workflow_instance(db, invoice)

    extraction_enabled = await is_step_enabled(db, vendor.organization_id, "extraction")
    source_details = {
        "actor_type": "vendor_user",
        "vendor_user_id": str(vu.id),
        "vendor_id": str(vendor.id),
        "source": "supplier_portal",
        "filename": file.filename,
        "content_type": file.content_type,
    }

    if extraction_enabled:
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.pending,
            actor_id=None,  # vendor user — not an AP user
            action_name="invoice.submitted_by_vendor",
            details=source_details,
        )
        await create_workflow_step(db, instance, "upload")
        await db.commit()
        await db.refresh(invoice)

        await dispatch_extraction(invoice.id, vendor.organization_id, None)

        await dispatch_audit(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=vendor.organization_id,
            actor_id=None,
            action="invoice.extraction_dispatched",
            entity_type="invoice",
            entity_id=invoice.id,
            details={"trigger": "supplier_portal", **source_details},
        )

        message = "Invoice submitted. Processing in progress."
    else:
        await create_workflow_step(db, instance, "upload")
        await refresh_warnings(db, invoice)
        await dispatch_audit(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=vendor.organization_id,
            actor_id=None,
            action="invoice.submitted_by_vendor",
            entity_type="invoice",
            entity_id=invoice.id,
            details=source_details,
        )
        await db.commit()
        await db.refresh(invoice)
        message = "Invoice submitted. Awaiting AP review."

    return {
        "id": str(invoice.id),
        "correlation_id": str(invoice.correlation_id),
        "status": invoice.status.value,
        "message": message,
    }


# ---------- Payments ----------


@router.get("/payments", response_model=PortalPaymentListResponse)
async def list_my_payments(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Payments on invoices belonging to the caller's vendor. Joined to
    `invoices` to (a) filter on `vendor_id` and (b) surface the invoice number
    so the vendor can reconcile without an extra round trip."""
    query = (
        select(Payment, Invoice.invoice_number)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .where(Invoice.vendor_id == vu.vendor_id)
    )
    total_query = (
        select(func.count())
        .select_from(Payment)
        .join(Invoice, Payment.invoice_id == Invoice.id)
        .where(Invoice.vendor_id == vu.vendor_id)
    )
    total = (await db.execute(total_query)).scalar() or 0

    query = (
        query.order_by(Payment.created_at.desc()).offset(pagination.offset).limit(pagination.limit)
    )
    rows = (await db.execute(query)).all()

    return PortalPaymentListResponse(
        items=[
            PortalPaymentListItem(
                id=str(p.id),
                invoice_id=str(p.invoice_id),
                invoice_number=inv_num or "",
                amount=p.amount,
                method=p.method,
                status=p.status,
                reference=p.reference,
                submitted_at=p.submitted_at,
                completed_at=p.completed_at,
            )
            for p, inv_num in rows
        ],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


# ---------- Purchase orders + PO flip ----------


@router.get("/purchase-orders", response_model=PortalPOListResponse)
async def list_my_purchase_orders(
    pagination: PaginationParams = Depends(pagination_params),
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """POs owned by the caller's vendor. The only filter is
    `PurchaseOrder.vendor_id == vu.vendor_id` — same vendor-scoping
    discipline as the invoice list."""
    line_count = (
        select(POLineItem.po_id, func.count().label("n")).group_by(POLineItem.po_id).subquery()
    )
    query = (
        select(PurchaseOrder, func.coalesce(line_count.c.n, 0))
        .outerjoin(line_count, line_count.c.po_id == PurchaseOrder.id)
        .where(PurchaseOrder.vendor_id == vu.vendor_id)
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(PurchaseOrder)
            .where(PurchaseOrder.vendor_id == vu.vendor_id)
        )
    ).scalar() or 0

    query = (
        query.order_by(PurchaseOrder.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = (await db.execute(query)).all()

    return PortalPOListResponse(
        items=[
            PortalPOListItem(
                id=str(po.id),
                po_number=po.po_number,
                status=po.status,
                total=po.total,
                line_item_count=n,
                created_at=po.created_at,
            )
            for po, n in rows
        ],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.get("/purchase-orders/{po_id}", response_model=PortalPODetail)
async def get_my_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    po = (
        await db.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.id == po_id, PurchaseOrder.vendor_id == vu.vendor_id
            )
        )
    ).scalar_one_or_none()
    if not po:
        # 404 (not 403) for cross-vendor IDs so the portal can't probe PO ids.
        raise HTTPException(status_code=404, detail="Purchase order not found")

    lines = (await db.execute(select(POLineItem).where(POLineItem.po_id == po.id))).scalars().all()

    return PortalPODetail(
        id=str(po.id),
        po_number=po.po_number,
        status=po.status,
        total=po.total,
        created_at=po.created_at,
        line_items=[
            PortalPOLineItem(
                description=li.description,
                quantity=li.quantity,
                unit_price=li.unit_price,
                total=li.total,
            )
            for li in lines
        ],
    )


@router.post("/purchase-orders/{po_id}/flip", status_code=status.HTTP_202_ACCEPTED)
async def flip_purchase_order(
    po_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PortalFlipResponse:
    """PO flip — create an invoice pre-populated from a PO the caller's
    vendor owns, then route it into the existing extraction/workflow path
    exactly like a normal supplier submission.

    Idempotency (project invariant #2 — this seeds the AP→payment pipeline):
    a flip is short-circuited if an invoice already exists for this PO from
    this vendor whose source is the PO flip. The optional ``Idempotency-Key``
    header is recorded on the audit breadcrumb for client-side replay
    correlation, but the durable guard is the existing-invoice check so a
    double-click can never mint two invoices off one PO.
    """
    po = (
        await db.execute(
            select(PurchaseOrder).where(
                PurchaseOrder.id == po_id, PurchaseOrder.vendor_id == vu.vendor_id
            )
        )
    ).scalar_one_or_none()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # Idempotency guard: an invoice already flipped from this PO by this
    # vendor short-circuits to that invoice rather than creating a duplicate.
    existing = (
        await db.execute(
            select(Invoice).where(
                Invoice.vendor_id == vu.vendor_id,
                Invoice.po_number == po.po_number,
                Invoice.reference_number == f"po-flip:{po.id}",
            )
        )
    ).scalar_one_or_none()
    if existing:
        return PortalFlipResponse(
            id=str(existing.id),
            correlation_id=str(existing.correlation_id),
            status=existing.status.value,
            message="Invoice already created from this PO.",
        )

    po_lines = (
        (await db.execute(select(POLineItem).where(POLineItem.po_id == po.id))).scalars().all()
    )

    invoice = Invoice(
        invoice_number="",
        vendor_name=vendor.name,
        vendor_id=vendor.id,
        description=f"Created from {po.po_number}",
        amount=po.total,
        currency="USD",
        status=InvoiceStatus.new,
        po_number=po.po_number,
        # Stable per-PO marker — drives the idempotency guard above.
        reference_number=f"po-flip:{po.id}",
        organization_id=vendor.organization_id,
        # Inherit the PO's entity so the flipped invoice stays in the same
        # subsidiary as the order it came from (multi-entity Phase 2).
        entity_id=po.entity_id,
    )
    db.add(invoice)
    try:
        await db.flush()
    except IntegrityError:
        # A concurrent flip of the same PO won the race: the partial unique
        # index on the `po-flip:<po_id>` marker (migration 0024) rejected this
        # duplicate before it could enter the AP→payment pipeline. Roll back and
        # return the same idempotent short-circuit as the fast-path check above.
        await db.rollback()
        existing = (
            await db.execute(select(Invoice).where(Invoice.reference_number == f"po-flip:{po.id}"))
        ).scalar_one_or_none()
        if existing:
            return PortalFlipResponse(
                id=str(existing.id),
                correlation_id=str(existing.correlation_id),
                status=existing.status.value,
                message="Invoice already created from this PO.",
            )
        raise

    for idx, li in enumerate(po_lines, start=1):
        db.add(
            InvoiceLineItem(
                invoice_id=invoice.id,
                line_number=idx,
                description=li.description,
                quantity=li.quantity,
                unit_price=li.unit_price,
                total=li.total,
            )
        )

    instance = await create_workflow_instance(db, invoice)
    extraction_enabled = await is_step_enabled(db, vendor.organization_id, "extraction")
    source_details = {
        "actor_type": "vendor_user",
        "vendor_user_id": str(vu.id),
        "vendor_id": str(vendor.id),
        "source": "supplier_portal_po_flip",
        "po_id": str(po.id),
        "po_number": po.po_number,
        "idempotency_key": idempotency_key,
    }

    if extraction_enabled:
        await transition_invoice(
            db,
            invoice,
            InvoiceStatus.pending,
            actor_id=None,
            action_name="invoice.created_from_po",
            details=source_details,
        )
        await create_workflow_step(db, instance, "upload")
        await db.commit()
        await db.refresh(invoice)
        await dispatch_extraction(invoice.id, vendor.organization_id, None)
        await dispatch_audit(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=vendor.organization_id,
            actor_id=None,
            action="invoice.extraction_dispatched",
            entity_type="invoice",
            entity_id=invoice.id,
            details={"trigger": "supplier_portal_po_flip", **source_details},
        )
        message = "Invoice created from PO. Processing in progress."
    else:
        await create_workflow_step(db, instance, "upload")
        await dispatch_audit(
            db,
            correlation_id=invoice.correlation_id,
            organization_id=vendor.organization_id,
            actor_id=None,
            action="invoice.created_from_po",
            entity_type="invoice",
            entity_id=invoice.id,
            details=source_details,
        )
        await refresh_warnings(db, invoice)
        await db.commit()
        await db.refresh(invoice)
        message = "Invoice created from PO. Awaiting AP review."

    return PortalFlipResponse(
        id=str(invoice.id),
        correlation_id=str(invoice.correlation_id),
        status=invoice.status.value,
        message=message,
    )


# ---------- Remittance ----------


@router.get("/payments/{payment_id}/remittance")
async def get_my_payment_remittance(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctrl_db: AsyncSession = Depends(get_control_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Vendor-scoped remittance-advice PDF. Ownership is enforced by the
    `Invoice.vendor_id == vu.vendor_id` join — a payment on another vendor's
    invoice returns 404, never a foreign PDF."""
    from app.services.remittance_pdf import (
        RemittanceContext,
        RemittanceLine,
        render_remittance_pdf,
    )

    row = (
        await db.execute(
            select(Payment, Invoice)
            .join(Invoice, Payment.invoice_id == Invoice.id)
            .where(Payment.id == payment_id, Invoice.vendor_id == vu.vendor_id)
        )
    ).one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Payment not found")
    payment, invoice = row

    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()

    # The payer (the AP customer) is an Organization in the control plane —
    # portal sessions don't carry one, so resolve it by the invoice's org_id.
    # Use the injected control session (not the module-global factory) so this
    # rides the request's event loop — reaching for the global engine directly
    # breaks under async test loops and bypasses dependency overrides.
    from app.models.organization import Organization

    org = (
        await ctrl_db.execute(
            select(Organization).where(Organization.id == invoice.organization_id)
        )
    ).scalar_one_or_none()
    company = (org.settings or {}).get("company") if org else None

    ctx = RemittanceContext(
        payer_name=org.name if org else "Your customer",
        payer_address=(company or {}).get("address"),
        vendor_name=invoice.vendor_name,
        vendor_address=(vendor.address if vendor else None) or invoice.remit_to_address,
        payment_date=payment.completed_at or payment.submitted_at or payment.created_at,
        payment_method=payment.method or "ach",
        payment_reference=payment.reference,
        payment_amount=payment.amount,
        currency=invoice.currency,
        lines=[
            RemittanceLine(
                invoice_number=invoice.invoice_number or str(payment.invoice_id),
                description=invoice.description,
                amount=payment.amount,
            )
        ],
    )
    pdf_bytes = render_remittance_pdf(ctx)
    filename = f"remittance-{payment.reference or str(payment.id)[:8]}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- Company self-service ----------


async def _pending_change(db: AsyncSession, vendor_id: uuid.UUID) -> VendorChangeRequest | None:
    return (
        (
            await db.execute(
                select(VendorChangeRequest)
                .where(
                    VendorChangeRequest.vendor_id == vendor_id,
                    VendorChangeRequest.status == "pending",
                )
                .order_by(VendorChangeRequest.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


@router.get("/company", response_model=PortalCompanyInfoResponse)
async def get_my_company(
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    pending = await _pending_change(db, vendor.id)
    return PortalCompanyInfoResponse(
        name=vendor.name,
        email=vendor.email,
        phone=vendor.phone,
        address=vendor.address,
        tax_id_last4=_last4(vendor.tax_id),
        has_bank_details=bool(vendor.bank_details),
        pending_change=(
            PortalPendingChange(
                id=str(pending.id),
                change_type=pending.change_type,
                status=pending.status,
                created_at=pending.created_at,
            )
            if pending
            else None
        ),
    )


@router.patch("/company", response_model=PortalCompanyInfoResponse)
async def update_my_company(
    body: PortalCompanyInfoUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Live-apply the non-sensitive contact fields (phone, address, email).
    Bank details and tax ID are NOT accepted here — they stage via the
    dedicated change-request endpoints and apply only after AP approval."""
    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    payload = body.model_dump(exclude_unset=True)
    changed = sorted(payload.keys())
    for field, value in payload.items():
        setattr(vendor, field, value)
    await db.flush()

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=vendor.organization_id,
        actor_id=None,
        action="vendor.contact_updated_by_vendor",
        entity_type="vendor",
        entity_id=vendor.id,
        # Log only which fields changed, never the values (address is PII).
        details={
            "actor_type": "vendor_user",
            "vendor_user_id": str(vu.id),
            "fields": changed,
        },
    )
    await db.commit()
    await db.refresh(vendor)

    pending = await _pending_change(db, vendor.id)
    return PortalCompanyInfoResponse(
        name=vendor.name,
        email=vendor.email,
        phone=vendor.phone,
        address=vendor.address,
        tax_id_last4=_last4(vendor.tax_id),
        has_bank_details=bool(vendor.bank_details),
        pending_change=(
            PortalPendingChange(
                id=str(pending.id),
                change_type=pending.change_type,
                status=pending.status,
                created_at=pending.created_at,
            )
            if pending
            else None
        ),
    )


async def _stage_change(
    db: AsyncSession,
    *,
    vu: VendorUser,
    vendor: Vendor,
    change_type: str,
    proposed_value: dict,
    last4: str | None,
) -> VendorChangeRequest:
    """Insert a pending change request. Deduped on
    `(vendor_id, change_type, status=pending)` so a vendor can't stack
    duplicate pending requests of the same type."""
    dup = (
        await db.execute(
            select(VendorChangeRequest).where(
                VendorChangeRequest.vendor_id == vendor.id,
                VendorChangeRequest.change_type == change_type,
                VendorChangeRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(
            status_code=409,
            detail="A change of this type is already pending AP review.",
        )

    req = VendorChangeRequest(
        vendor_id=vendor.id,
        organization_id=vendor.organization_id,
        requested_by_vendor_user_id=vu.id,
        change_type=change_type,
        status="pending",
        proposed_value=proposed_value,
    )
    db.add(req)
    await db.flush()

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=vendor.organization_id,
        actor_id=None,
        action=f"vendor.{change_type}_change_requested",
        entity_type="vendor",
        entity_id=vendor.id,
        # PII guard: never log the proposed value — only a last-4 + the id.
        details={
            "actor_type": "vendor_user",
            "vendor_user_id": str(vu.id),
            "change_type": change_type,
            "request_id": str(req.id),
            "last4": last4,
        },
    )
    await db.commit()
    await db.refresh(req)
    return req


@router.post(
    "/company/bank-change",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PortalChangeRequestResponse,
)
async def request_bank_change(
    body: PortalBankChangeRequest,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Stage a `bank_details` change. The vendor row is NOT mutated — the
    change has zero effect on where money goes until AP approves it."""
    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if not body.bank_details:
        raise HTTPException(status_code=400, detail="bank_details is required")

    account = str(body.bank_details.get("account_number") or "")
    last4 = account[-4:] if len(account) >= 4 else None
    req = await _stage_change(
        db,
        vu=vu,
        vendor=vendor,
        change_type="bank_details",
        proposed_value={"bank_details": body.bank_details},
        last4=last4,
    )
    return PortalChangeRequestResponse(
        id=str(req.id),
        change_type=req.change_type,
        status=req.status,
        created_at=req.created_at,
    )


@router.post(
    "/company/tax-id-change",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PortalChangeRequestResponse,
)
async def request_tax_id_change(
    body: PortalTaxIdChangeRequest,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Stage a `tax_id` change. Re-keys 1099 reporting on approval, so it
    routes through AP review the same as a bank change — never applied live."""
    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    req = await _stage_change(
        db,
        vu=vu,
        vendor=vendor,
        change_type="tax_id",
        proposed_value={"tax_id": body.tax_id},
        last4=_last4(body.tax_id),
    )
    return PortalChangeRequestResponse(
        id=str(req.id),
        change_type=req.change_type,
        status=req.status,
        created_at=req.created_at,
    )


@router.get("/company/change-requests", response_model=list[PortalChangeRequestResponse])
async def list_my_change_requests(
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    rows = (
        (
            await db.execute(
                select(VendorChangeRequest)
                .where(VendorChangeRequest.vendor_id == vu.vendor_id)
                .order_by(VendorChangeRequest.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        PortalChangeRequestResponse(
            id=str(r.id),
            change_type=r.change_type,
            status=r.status,
            created_at=r.created_at,
        )
        for r in rows
    ]


# ---------- Early-payment discount offers ----------
#
# A vendor sees the early-payment discount offers the AP team has extended to
# them (offers scoped to their own vendor_id, and offers on their own invoices)
# and can ACCEPT the offered early-pay discount. Accepting only flips the offer
# status to `accepted` (reusing `discount_offers.accept_offer`) — it never moves
# money; the CFO-gated payment run still funds it. Re-accepting an already-
# accepted (or otherwise non-`offered`) offer is a no-op 409, mirroring the AP
# side. Every offer query is filtered to the caller's own vendor, and a 404
# (never 403) is returned for a foreign offer so the portal can't probe IDs.


def _offer_tier(base_amount: Decimal, tier: dict | None) -> PortalDiscountTier | None:
    """Turn a stored tier dict into the vendor-facing tier (with savings)."""
    if not tier:
        return None
    return PortalDiscountTier(
        days=int(tier["days"]),
        percent=offers_svc.tier_percent(tier),
        savings=offers_svc.discount_savings(base_amount, tier),
    )


def _portal_offer_response(
    offer: DiscountOffer, *, invoice_number: str | None, today: date
) -> PortalDiscountOfferResponse:
    tiers = [_offer_tier(offer.base_amount, t) for t in (offer.tiers or [])]
    # Best capturable tier today is only meaningful while the offer is still open.
    best_tier = None
    if offer.status == OFFER_STATUS_OFFERED:
        best = offers_svc.best_tier_for_date(offer.tiers or [], today, offer.valid_until)
        best_tier = _offer_tier(offer.base_amount, best)
    return PortalDiscountOfferResponse(
        id=str(offer.id),
        status=offer.status,
        scope=offer.scope,
        invoice_id=str(offer.invoice_id) if offer.invoice_id else None,
        invoice_number=invoice_number,
        base_amount=offer.base_amount,
        currency=offer.currency,
        tiers=[t for t in tiers if t is not None],
        best_tier=best_tier,
        valid_from=offer.valid_from,
        valid_until=offer.valid_until,
        accepted_tier=_offer_tier(offer.base_amount, offer.accepted_tier),
        accepted_at=offer.accepted_at,
        captured_amount=offer.captured_amount,
        captured_at=offer.captured_at,
        notes=offer.notes,
        created_at=offer.created_at,
    )


def _vendor_offer_filter(vu: VendorUser):
    """Predicate restricting offers to the calling vendor.

    A vendor sees offers explicitly scoped to their `vendor_id`, plus offers on
    one of their own invoices (invoice-scoped offers carry the invoice_id, not a
    vendor_id). The own-invoices set is an in-query subquery so the data layer —
    not just app code — enforces the scoping (project invariant: tenant/vendor
    isolation at the data layer)."""
    own_invoice_ids = select(Invoice.id).where(Invoice.vendor_id == vu.vendor_id)
    return (DiscountOffer.vendor_id == vu.vendor_id) | (
        DiscountOffer.invoice_id.in_(own_invoice_ids)
    )


async def _portal_offer_or_404(
    db: AsyncSession, offer_id: uuid.UUID, vu: VendorUser
) -> DiscountOffer:
    """Fetch an offer the calling vendor owns, else 404 (never 403)."""
    offer = (
        await db.execute(
            select(DiscountOffer).where(
                DiscountOffer.id == offer_id,
                _vendor_offer_filter(vu),
            )
        )
    ).scalar_one_or_none()
    if offer is None:
        raise HTTPException(status_code=404, detail="Discount offer not found")
    return offer


async def _invoice_numbers(db: AsyncSession, offers: list[DiscountOffer]) -> dict[uuid.UUID, str]:
    invoice_ids = {o.invoice_id for o in offers if o.invoice_id}
    if not invoice_ids:
        return {}
    rows = await db.execute(
        select(Invoice.id, Invoice.invoice_number).where(Invoice.id.in_(invoice_ids))
    )
    return {iid: (num or "") for iid, num in rows.all()}


@router.get("/discount-offers", response_model=PortalDiscountOfferListResponse)
async def list_my_discount_offers(
    pagination: PaginationParams = Depends(pagination_params),
    status_filter: str | None = Query(None, alias="status"),
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Early-payment discount offers relevant to the calling vendor.

    Scoped to the vendor's own `vendor_id` and to offers on their own invoices —
    a vendor can never see another vendor's offers. Includes the per-tier
    savings + the best capturable tier today so the supplier sees the ROI of
    accepting. Optional `?status=` filter (e.g. `offered`)."""
    query = select(DiscountOffer).where(_vendor_offer_filter(vu))
    if status_filter:
        wanted = [s.strip() for s in status_filter.split(",") if s.strip()]
        if wanted:
            query = query.where(DiscountOffer.status.in_(wanted))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = (
        query.order_by(DiscountOffer.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = list((await db.execute(query)).scalars().all())
    inv_nums = await _invoice_numbers(db, rows)
    today = date.today()

    return PortalDiscountOfferListResponse(
        items=[
            _portal_offer_response(
                o, invoice_number=inv_nums.get(o.invoice_id) if o.invoice_id else None, today=today
            )
            for o in rows
        ],
        total=total,
        page=pagination.page,
        page_size=pagination.page_size,
    )


@router.post("/discount-offers/{offer_id}/accept", response_model=PortalDiscountOfferResponse)
async def accept_my_discount_offer(
    offer_id: uuid.UUID,
    body: PortalAcceptOfferRequest | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Vendor accepts an offered early-payment discount.

    Reuses `discount_offers.accept_offer` (the same pure mutator the AP side
    uses), which flips `offered → accepted` and records the chosen tier. This
    NEVER moves money — no `Payment`/`PaymentRun` is created; the CFO-gated
    payment run still funds the early payment. Idempotency: re-accepting a
    non-`offered` offer raises `409` (the status guard is the dedupe), so a
    double-click can't double-count savings."""
    offer = await _portal_offer_or_404(db, offer_id, vu)
    today = date.today()

    tier_days = body.tier_days if body else None
    if tier_days is not None:
        tier = offers_svc.select_tier(offer.tiers or [], tier_days)
        if tier is None:
            raise HTTPException(status_code=422, detail="No tier matches the requested days")
    else:
        tier = offers_svc.best_tier_for_date(offer.tiers or [], today, offer.valid_until)
        if tier is None:
            raise HTTPException(status_code=409, detail="Offer has no capturable tier today")

    try:
        offers_svc.accept_offer(offer, tier=tier, actor_id=None, now=datetime.now(UTC))
    except ValueError as exc:
        # Not in `offered` status (already accepted / declined / expired / captured).
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Audit — actor_id=None (a VendorUser is not a control-plane user); PII-free.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=offer.organization_id,
        actor_id=None,
        action="discount_offer.accepted_by_vendor",
        entity_type="discount_offer",
        entity_id=offer.id,
        details={
            "actor_type": "vendor_user",
            "vendor_user_id": str(vu.id),
            "vendor_id": str(vu.vendor_id),
            "tier": offer.accepted_tier,
        },
    )
    await db.commit()
    await db.refresh(offer)

    invoice_number = None
    if offer.invoice_id:
        invoice_number = (await _invoice_numbers(db, [offer])).get(offer.invoice_id)
    return _portal_offer_response(offer, invoice_number=invoice_number, today=today)


@router.post("/discount-offers/{offer_id}/decline", response_model=PortalDiscountOfferResponse)
async def decline_my_discount_offer(
    offer_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Vendor declines an offered early-payment discount. Reuses
    `discount_offers.decline_offer`; `409` if the offer is no longer `offered`."""
    offer = await _portal_offer_or_404(db, offer_id, vu)
    try:
        offers_svc.decline_offer(offer, now=datetime.now(UTC))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=offer.organization_id,
        actor_id=None,
        action="discount_offer.declined_by_vendor",
        entity_type="discount_offer",
        entity_id=offer.id,
        details={
            "actor_type": "vendor_user",
            "vendor_user_id": str(vu.id),
            "vendor_id": str(vu.vendor_id),
        },
    )
    await db.commit()
    await db.refresh(offer)

    today = date.today()
    invoice_number = None
    if offer.invoice_id:
        invoice_number = (await _invoice_numbers(db, [offer])).get(offer.invoice_id)
    return _portal_offer_response(offer, invoice_number=invoice_number, today=today)


# ---------- Card reveal (no auth — token is the credential) ----------


@router.get("/cards/{token}")
async def reveal_card(
    token: str,
    db: AsyncSession = Depends(get_tenant_db),
):
    """Vendor-facing single-use card reveal.

    Resolves the URL-safe token, returns the live PAN/CVV/expiry on the
    first hit, and marks the token used so a second visit returns
    `gone`. The token itself is the credential — no portal-auth dep
    here. Tokens expire after 7 days regardless of use.

    Errors come back as small JSON bodies with stable shapes so the
    portal UI can show a sensible message:
      404 invalid    — wrong / unknown token
      410 used       — already revealed once
      410 expired    — past the expiry window
    """
    from app.services.audit_dispatch import dispatch_audit
    from app.services.card_adapters import (
        get_card_adapter,
    )
    from app.services.card_reveal import consume_reveal_token

    card, error = await consume_reveal_token(db, token)
    if error == "invalid":
        raise HTTPException(status_code=404, detail="Invalid token")
    if error == "expired":
        raise HTTPException(status_code=410, detail="This link has expired")
    if error == "used":
        raise HTTPException(status_code=410, detail="This link has already been used")
    assert card is not None  # narrowing for mypy

    # Re-fetch + decrypt the PAN via the adapter, same path the
    # /api/cards/{id}/details endpoint uses internally.
    org_settings_result = await db.execute(
        select(Vendor).where(Vendor.id == card.vendor_id)
        if card.vendor_id
        else select(Vendor).limit(0)
    )
    _ = org_settings_result.scalar_one_or_none()  # not strictly needed; left for parity

    # Build a config that matches the cards section of the org settings.
    # Loading the Organization here is unusual — portal sessions don't
    # carry one — so we read it directly via the org_id on the card.
    from app.config import settings as app_settings
    from app.database import control_session_factory
    from app.models.organization import Organization

    async with control_session_factory() as ctrl:
        org_row = await ctrl.execute(
            select(Organization).where(Organization.id == card.organization_id)
        )
        org = org_row.scalar_one_or_none()

    from app.services.card_issuance import _resolve_card_config

    config = _resolve_card_config(org.settings if org else {}, app_settings)
    if config is None:
        # Org disabled cards after issuance — surface the saved last4
        # only so the vendor still has something to call about.
        await db.commit()
        return {
            "last_four": card.last_four,
            "amount_limit": float(card.amount_limit),
            "currency": card.currency,
            "expires_at": card.expires_at.isoformat() if card.expires_at else None,
            "pan": None,
            "cvv": None,
            "warning": "Card details are no longer available for retrieval.",
        }

    import app.services.card_adapters.lithic  # noqa: F401
    import app.services.card_adapters.mock_adapter  # noqa: F401
    import app.services.card_adapters.nium  # noqa: F401

    adapter = get_card_adapter(config)
    try:
        details = await adapter.get_card_details(card.provider_card_id)
    except Exception:  # noqa: BLE001
        # Adapter outage — return the saved metadata so the vendor at
        # least sees the link worked, and tell them to contact AP.
        await db.commit()
        return {
            "last_four": card.last_four,
            "amount_limit": float(card.amount_limit),
            "currency": card.currency,
            "expires_at": card.expires_at.isoformat() if card.expires_at else None,
            "pan": None,
            "cvv": None,
            "warning": "Card details are temporarily unavailable. Please contact AP.",
        }

    await dispatch_audit(
        db,
        correlation_id=card.correlation_id or uuid.uuid4(),
        organization_id=card.organization_id,
        actor_id=None,  # vendor reveal — no internal user
        action="card.revealed_via_token",
        entity_type="virtual_card",
        entity_id=card.id,
        details={"last_four": card.last_four},
    )
    await db.commit()

    return {
        "last_four": card.last_four,
        "amount_limit": float(card.amount_limit),
        "currency": card.currency,
        "expires_at": card.expires_at.isoformat() if card.expires_at else None,
        "pan": details.pan,
        "cvv": details.cvv,
    }


# ---------- Supplier chat (portal side) ----------
#
# Vendor-scoped per-invoice chat. The supplier sees AP author names only (never
# an internal users.id). No mentions, no templates, no resolve/reopen here.
# Datetimes are raw `datetime` (portal convention). See
# backend/docs/supplier-chat.md.


def _portal_chat_message_to_response(msg: SupplierChatMessage) -> PortalChatMessageResponse:
    return PortalChatMessageResponse(
        id=str(msg.id),
        author_role=str(msg.author_role),
        author_name=msg.author_name,
        body=msg.body,
        attachments=[
            PortalChatAttachmentOut(
                file_url=a.get("file_url", ""),
                filename=a.get("filename", ""),
                content_type=a.get("content_type", ""),
                size=a.get("size", 0),
            )
            for a in (msg.attachments or [])
        ],
        created_at=msg.created_at,
    )


async def _portal_invoice_or_404(
    db: AsyncSession, invoice_id: uuid.UUID, vu: VendorUser
) -> Invoice:
    inv = (
        await db.execute(
            select(Invoice).where(Invoice.id == invoice_id, Invoice.vendor_id == vu.vendor_id)
        )
    ).scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return inv


async def _portal_org(ctrl_db: AsyncSession, organization_id: uuid.UUID):
    from app.models.organization import Organization

    return (
        await ctrl_db.execute(select(Organization).where(Organization.id == organization_id))
    ).scalar_one_or_none()


@router.get("/invoices/{invoice_id}/chat", response_model=PortalChatThreadResponse)
async def get_portal_chat(
    invoice_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctrl_db: AsyncSession = Depends(get_control_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    inv = await _portal_invoice_or_404(db, invoice_id, vu)
    org = await _portal_org(ctrl_db, inv.organization_id)
    if not chat_enabled(org):
        return PortalChatThreadResponse(
            invoice_id=str(inv.id), status=ChatThreadStatus.open.value, messages=[]
        )
    thread = await get_thread(db, inv.id)
    if thread is None:
        return PortalChatThreadResponse(
            invoice_id=str(inv.id), status=ChatThreadStatus.open.value, messages=[]
        )
    messages = await list_messages(db, thread.id)
    return PortalChatThreadResponse(
        invoice_id=str(inv.id),
        status=str(thread.status),
        messages=[_portal_chat_message_to_response(m) for m in messages],
    )


async def _post_portal_chat_message(
    db: AsyncSession,
    inv: Invoice,
    vu: VendorUser,
    *,
    body: str,
    attachments: list[dict] | None,
) -> PortalChatMessageResponse:
    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == vu.vendor_id))
    ).scalar_one_or_none()
    author_name = vu.full_name or (vendor.name if vendor else None)

    thread = await get_or_create_thread(db, inv)
    msg = SupplierChatMessage(
        thread_id=thread.id,
        author_role=ChatAuthorRole.supplier,
        author_user_id=vu.id,
        author_name=author_name,
        body=body,
        mentions=None,
        attachments=attachments or None,
        template_key=None,
    )
    db.add(msg)
    await db.flush()

    # Audit — actor_id=None (a VendorUser is not a control-plane user). PII-free.
    await dispatch_audit(
        db,
        correlation_id=inv.correlation_id,
        organization_id=inv.organization_id,
        actor_id=None,
        action="chat_message_posted",
        entity_type="invoice",
        entity_id=inv.id,
        details={
            "thread_id": str(thread.id),
            "message_id": str(msg.id),
            "author_role": str(msg.author_role),
            "has_attachment": bool(msg.attachments),
            "template_key": msg.template_key,
        },
    )

    # Notify the org's AP managers (control-plane Users only). Pre-commit.
    await notify_supplier_post(db, invoice=inv)

    await db.commit()
    await db.refresh(msg)
    return _portal_chat_message_to_response(msg)


@router.post(
    "/invoices/{invoice_id}/chat",
    response_model=PortalChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_portal_chat(
    invoice_id: uuid.UUID,
    payload: PortalChatMessageCreate,
    db: AsyncSession = Depends(get_tenant_db),
    ctrl_db: AsyncSession = Depends(get_control_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    inv = await _portal_invoice_or_404(db, invoice_id, vu)
    org = await _portal_org(ctrl_db, inv.organization_id)
    if not chat_enabled(org):
        raise HTTPException(status_code=403, detail="Supplier chat is disabled")
    return await _post_portal_chat_message(db, inv, vu, body=payload.body, attachments=None)


@router.post(
    "/invoices/{invoice_id}/chat/attachments",
    response_model=PortalChatMessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_portal_chat_attachment(
    invoice_id: uuid.UUID,
    file: UploadFile = File(...),
    body: str = Form(default=""),
    db: AsyncSession = Depends(get_tenant_db),
    ctrl_db: AsyncSession = Depends(get_control_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    inv = await _portal_invoice_or_404(db, invoice_id, vu)
    org = await _portal_org(ctrl_db, inv.organization_id)
    if not chat_enabled(org):
        raise HTTPException(status_code=403, detail="Supplier chat is disabled")

    message_id = uuid.uuid4()
    try:
        file_key, filename, content_type, size = await upload_chat_file(
            inv.organization_id, inv.id, message_id, file
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    attachment = {
        "file_key": file_key,
        "file_url": f"/api/portal/invoices/{inv.id}/chat/file/{file_key}",
        "filename": filename,
        "content_type": content_type,
        "size": size,
    }
    return await _post_portal_chat_message(
        db, inv, vu, body=body or filename, attachments=[attachment]
    )


@router.get("/invoices/{invoice_id}/chat/file/{file_key:path}")
async def get_portal_chat_file(
    invoice_id: uuid.UUID,
    file_key: str,
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    """Download a chat attachment. The invoice must be the vendor's own, and the
    key's leading org segment must match the invoice's org — wrong-org / missing
    both 404 (no enumeration)."""
    inv = await _portal_invoice_or_404(db, invoice_id, vu)
    prefix = file_key.split("/", 1)[0]
    if prefix != str(inv.organization_id):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content, content_type = get_file(file_key)
    except Exception:
        raise HTTPException(status_code=404, detail="File not found")
    return Response(content=content, media_type=content_type)
