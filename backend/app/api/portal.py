"""Supplier portal — invoice submission/listing + payment history.

Every endpoint is vendor-scoped: the caller's `vendor_id` (from the JWT via
`get_current_vendor_user`) is the only vendor ID the handler ever filters on.
A vendor user cannot reference another vendor's invoices even by guessing IDs.
"""

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.portal_deps import get_current_vendor_user
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser
from app.models.virtual_card import VirtualCard
from app.schemas.portal import (
    PortalInvoiceListItem,
    PortalInvoiceListResponse,
    PortalPaymentListItem,
    PortalPaymentListResponse,
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

router = APIRouter(prefix="/portal", tags=["portal"])


# ---------- Invoices ----------


@router.get("/invoices", response_model=PortalInvoiceListResponse)
async def list_my_invoices(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    db: AsyncSession = Depends(get_tenant_db),
    vu: VendorUser = Depends(get_current_vendor_user),
):
    query = select(Invoice).where(Invoice.vendor_id == vu.vendor_id)
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0

    query = (
        query.order_by(Invoice.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
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
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
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
        query.order_by(Payment.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
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
    )


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
        VirtualCardPayload,
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
        select(Vendor).where(Vendor.id == card.vendor_id) if card.vendor_id else select(Vendor).limit(0)
    )
    _ = org_settings_result.scalar_one_or_none()  # not strictly needed; left for parity

    # Build a config that matches the cards section of the org settings.
    # Loading the Organization here is unusual — portal sessions don't
    # carry one — so we read it directly via the org_id on the card.
    from app.models.organization import Organization
    from app.config import settings as app_settings
    from app.database import control_session_factory

    async with control_session_factory() as ctrl:
        org_row = await ctrl.execute(
            select(Organization).where(Organization.id == card.organization_id)
        )
        org = org_row.scalar_one_or_none()
    org_cards = (org.settings or {}).get("cards") if org and org.settings else None

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
