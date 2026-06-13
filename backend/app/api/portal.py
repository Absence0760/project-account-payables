"""Supplier portal — invoice submission/listing + payment history.

Every endpoint is vendor-scoped: the caller's `vendor_id` (from the JWT via
`get_current_vendor_user`) is the only vendor ID the handler ever filters on.
A vendor user cannot reference another vendor's invoices even by guessing IDs.
"""

import logging
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Header, HTTPException, Response, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.pagination import PaginationParams, pagination_params
from app.api.portal_deps import get_current_vendor_user
from app.database import get_control_db
from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.payment import Payment
from app.models.procurement import POLineItem, PurchaseOrder
from app.models.vendor import Vendor
from app.models.vendor_change_request import VendorChangeRequest
from app.models.vendor_user import VendorUser
from app.schemas.portal import (
    PortalBankChangeRequest,
    PortalChangeRequestResponse,
    PortalCompanyInfoResponse,
    PortalCompanyInfoUpdateRequest,
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
from app.services.audit_dispatch import dispatch_audit
from app.services.extraction_dispatch import dispatch_extraction
from app.services.invoice_warnings import refresh_warnings
from app.services.storage import upload_invoice_file
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
