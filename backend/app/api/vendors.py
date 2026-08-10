"""Vendor CRUD endpoints with verification workflow + portal-user management."""

import dataclasses
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_AP_CLERK,
    ROLE_AP_MANAGER,
    ROLE_CFO,
    get_org_id,
    require_permission,
    require_roles,
)
from app.api.pagination import PaginationParams, paginated, pagination_params
from app.api.permissions import (
    PERM_VENDOR_BANK_CHANGE_APPROVE,
    PERM_VENDOR_BLOCK,
    PERM_VENDOR_MANAGE,
)
from app.config import settings
from app.models.contract import Contract
from app.models.credit_memo import CreditMemo
from app.models.discount import DiscountOffer
from app.models.invoice import Invoice
from app.models.invoice_embedding import InvoiceEmbedding
from app.models.organization import Organization
from app.models.procurement import PurchaseOrder
from app.models.sanctions_check import SanctionsCheck
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_change_request import VendorChangeRequest
from app.models.vendor_priors import VendorExtractionPrior
from app.models.vendor_user import VendorUser
from app.models.virtual_card import VirtualCard
from app.schemas.portal import (
    PortalInviteRequest,
    PortalInviteResponse,
    PortalUserResponse,
)
from app.schemas.sanctions import (
    SanctionsCheckResponse,
    ScreeningReviewItem,
    VendorBlockRequest,
)
from app.schemas.vendor import (
    VendorBankChangeRequest,
    VendorChangeRequestResponse,
    VendorChangeReviewRequest,
    VendorCreate,
    VendorResponse,
    VendorUpdate,
)
from app.services.audit_access import build_field_diff, log_access
from app.services.audit_dispatch import dispatch_audit
from app.services.csv_import import MAX_CSV_IMPORT_SIZE, import_vendors_csv
from app.services.email_adapters import EmailMessage, get_email_adapter
from app.services.vendor_screening import screen_vendor_record
from app.services.vendor_sync import sync_vendors_from_erp
from app.tenant import (
    apply_entity_scope,
    get_entity_id,
    get_tenant,
    get_tenant_db,
    get_write_entity_id,
)
from app.utils.passwords import generate_temp_password
from app.utils.passwords import pwd_context as _pwd

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vendors", tags=["vendors"])


def _merge_bank_details(existing: dict | None, incoming: dict | None) -> dict | None:
    """Merge an incoming `bank_details` partial into the stored JSONB.

    The column has historically held arbitrary processor metadata, so a
    PATCH that only sets `counterparty_id` must not clobber sibling
    keys. Empty string and ``None`` are treated as "clear this key" so
    the UI can remove a counterparty without inventing a magic value.
    A fully-cleared dict is collapsed back to ``None``.
    """
    merged = dict(existing or {})
    for k, v in (incoming or {}).items():
        if v is None or v == "":
            merged.pop(k, None)
        else:
            merged[k] = v
    return merged or None


_IDENTITY_FIELDS = frozenset({"name", "tax_id", "bank_details", "beneficial_owner_data"})

# Scalar vendor fields whose before/after we record verbatim in the
# `vendor.updated` audit diff. `bank_details` and `tax_id` are handled
# separately because their raw values are PII / banking data and must be
# masked (last-4 only) — see `_bank_details_audit_summary` / the tax_id path.
_AUDITABLE_SCALAR_FIELDS = (
    "name",
    "code",
    "email",
    "phone",
    "address",
    "payment_terms",
    "accepts_virtual_cards",
    "status",
)

# Keys inside `bank_details` JSONB that hold raw banking secrets and must
# NEVER appear in an audit row. We record only THAT they changed plus a
# last-4 (PII-out-of-logs invariant). Every other key (counterparty_id,
# *_last4, bank_name, swift_bic, country) is non-secret display metadata.
_BANK_SECRET_KEYS = frozenset({"account_number", "routing_number", "iban"})


def _last4(value: object) -> str | None:
    s = str(value or "")
    return s[-4:] if len(s) >= 4 else None


async def _stage_ap_bank_change(
    db: AsyncSession,
    *,
    vendor: Vendor,
    incoming: dict,
    user: User,
    org_id: uuid.UUID,
) -> VendorChangeRequest:
    """Stage an AP-initiated bank-details change as a pending VendorChangeRequest
    instead of applying it — the BEC / bank-redirect dual-control gate.

    An AP user with `vendor.manage` can PROPOSE new bank details, but the change
    only takes effect when a SECOND user holding `vendor.bank_change.approve`
    approves it (and the approve path refuses the proposer — see
    `approve_change_request`). This closes the one-API-call bank redirect that an
    immediate PATCH allowed. Deduped on `(vendor, bank_details, pending)`, mirrors
    the supplier-portal `_stage_change`. The proposed value carries banking data
    and is never logged — the audit breadcrumb is `{change_type, request_id, last4}`.
    """
    dup = (
        await db.execute(
            select(VendorChangeRequest).where(
                VendorChangeRequest.vendor_id == vendor.id,
                VendorChangeRequest.change_type == "bank_details",
                VendorChangeRequest.status == "pending",
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(
            status_code=409,
            detail="A bank-details change is already pending approval for this vendor.",
        )

    incoming = incoming or {}
    last4 = _last4(incoming.get("account_number") or incoming.get("account_last4"))
    # PII-safe preview of what WOULD change if approved (last-4s only; raw
    # secrets masked by _bank_details_audit_summary). The change isn't applied
    # to the row here — this is for the audit breadcrumb only.
    proposed_preview = _merge_bank_details(vendor.bank_details, incoming)
    change_summary = _bank_details_audit_summary(vendor.bank_details, proposed_preview)
    req = VendorChangeRequest(
        vendor_id=vendor.id,
        organization_id=org_id,
        requested_by_user_id=user.id,
        change_type="bank_details",
        status="pending",
        proposed_value={"bank_details": incoming},
    )
    db.add(req)
    await db.flush()
    details = {
        "actor_type": "ap_user",
        "change_type": "bank_details",
        "request_id": str(req.id),
        "last4": last4,
    }
    if change_summary:
        details["proposed_change"] = change_summary
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.bank_details_change_requested",
        entity_type="vendor",
        entity_id=vendor.id,
        # PII guard: never log the proposed raw value — only masked last-4s + id.
        details=details,
    )
    return req


def _bank_details_audit_summary(before: dict | None, after: dict | None) -> dict | None:
    """PII-safe description of a `bank_details` change for the audit trail.

    Records the SET of keys that changed and, for the raw banking secrets
    (account/routing number, IBAN), only a masked last-4 of the old/new
    value — never the full number (PII / banking data must stay out of the
    audit trail). Non-secret display keys (counterparty_id, *_last4,
    bank_name, swift_bic, country) record their literal old/new values.
    Returns ``None`` when nothing changed.
    """
    before = before or {}
    after = after or {}
    all_keys = before.keys() | after.keys()
    changed_keys = sorted(k for k in all_keys if before.get(k) != after.get(k))
    if not changed_keys:
        return None

    field_changes: dict[str, dict] = {}
    for k in changed_keys:
        if k in _BANK_SECRET_KEYS:
            field_changes[k] = {
                "old_last4": _last4(before.get(k)),
                "new_last4": _last4(after.get(k)),
            }
        else:
            field_changes[k] = {"old": before.get(k), "new": after.get(k)}
    return {"changed_fields": changed_keys, "fields": field_changes}


async def _screen_best_effort(
    db: AsyncSession,
    *,
    vendor: Vendor,
    org: Organization,
    org_id: uuid.UUID,
    check_type: str,
    actor_id: uuid.UUID | None,
) -> None:
    """Run a sanctions screen for `vendor` without ever jeopardising the
    surrounding vendor write.

    Screening is a best-effort side effect: if the configured provider is
    down or raises, the vendor create/update must still succeed. The screen
    therefore runs inside a SAVEPOINT (`begin_nested`) so a mid-screen
    failure rolls back only the screen's partial mutations, leaving the
    vendor row intact, and the exception is logged + swallowed.
    """
    if not settings.vendor_screening_enabled:
        return
    try:
        async with db.begin_nested():
            await screen_vendor_record(
                db,
                vendor=vendor,
                organization_id=org_id,
                org_settings=org.settings,
                check_type=check_type,
                actor_id=actor_id,
            )
    except Exception as exc:  # noqa: BLE001
        # Log the exception type, never the message/traceback. A sanctions
        # adapter's error string could embed a vendor identifier; interpolating
        # `exc` (or exc_info=True) would push that into the log sink (invariant #7).
        logger.warning(
            "Sanctions screen failed for vendor=%s (check_type=%s) — vendor write preserved: %s",
            vendor.id,
            check_type,
            exc.__class__.__name__,
        )


# Invoices already cleared for payment (mirrors payments.PAYABLE_INVOICE_STATUSES).
# Defined locally to avoid importing the payments router into vendors.
_BANK_CHANGE_PAYABLE_STATUSES = (
    "approved",
    "posted_in_erp",
    "payment_scheduled",
)


async def _flag_payable_invoices_for_bank_change(db: AsyncSession, *, vendor: Vendor) -> None:
    """Raise a de-duped ``fraud_flag`` exception on every in-queue invoice for a
    vendor whose bank details just changed, forcing a human second look before
    the next payment run pays into the new account. Description is PII-free."""
    from app.models.exception import Exception as APException
    from app.services.exception_service import create_exception

    invoices = (
        (
            await db.execute(
                select(Invoice).where(
                    Invoice.vendor_id == vendor.id,
                    Invoice.status.in_(_BANK_CHANGE_PAYABLE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    for inv in invoices:
        # De-dupe: don't pile a second open fraud_flag on the same invoice.
        existing = (
            await db.execute(
                select(func.count()).where(
                    APException.invoice_id == inv.id,
                    APException.exception_type == "fraud_flag",
                    APException.status.in_(["open", "escalated"]),
                )
            )
        ).scalar() or 0
        if existing:
            continue
        await create_exception(
            db,
            exception_type="fraud_flag",
            severity="error",
            description="Vendor bank details changed; verify before payment.",
            status="open",
            organization_id=inv.organization_id,
            invoice=inv,
        )


@router.get("")
async def list_vendors(
    pagination: PaginationParams = Depends(pagination_params),
    search: str | None = None,
    status_filter: str | None = Query(None, alias="status"),
    source: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    query = apply_entity_scope(select(Vendor), Vendor, entity_id)
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Vendor.name.ilike(pattern) | Vendor.code.ilike(pattern) | Vendor.email.ilike(pattern)
        )
    if status_filter:
        statuses = [s.strip() for s in status_filter.split(",")]
        query = query.where(Vendor.status.in_(statuses))
    if source:
        query = query.where(Vendor.source == source)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    query = query.order_by(Vendor.name).offset(pagination.offset).limit(pagination.limit)
    result = await db.execute(query)
    vendors = result.scalars().all()

    # Get invoice counts per vendor
    items = []
    for v in vendors:
        count_result = await db.execute(select(func.count()).where(Invoice.vendor_id == v.id))
        inv_count = count_result.scalar() or 0
        items.append(VendorResponse.from_db(v, inv_count))

    return paginated(items, total, pagination)


@router.get("/counts")
async def vendor_status_counts(
    search: str | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Status tallies for the vendor filter chips.

    Computed over the WHOLE entity-scoped (and optionally searched) vendor set,
    not the loaded page — so the chip counts, and in particular the red
    "Unverified" attention badge, can't undercount when the list paginates past
    one page. Mirrors GET /api/invoices/counts. Registered before the
    `/{vendor_id}` route so the literal path isn't swallowed by the UUID param.
    """
    query = apply_entity_scope(
        select(Vendor.status, func.count()).select_from(Vendor), Vendor, entity_id
    )
    if search:
        pattern = f"%{search}%"
        query = query.where(
            Vendor.name.ilike(pattern) | Vendor.code.ilike(pattern) | Vendor.email.ilike(pattern)
        )
    query = query.group_by(Vendor.status)
    rows = (await db.execute(query)).all()
    by_status = {str(status): int(n) for status, n in rows}
    return {"total": sum(by_status.values()), "by_status": by_status}


async def _vendor_name(db: AsyncSession, vendor_id: uuid.UUID) -> str | None:
    return (
        await db.execute(select(Vendor.name).where(Vendor.id == vendor_id))
    ).scalar_one_or_none()


# Registered BEFORE the parametric `/{vendor_id}` route so the literal
# `/change-requests` path isn't swallowed by `vendor_id` (which would 422 on
# the non-UUID segment). FastAPI matches routes in declaration order.
@router.get("/change-requests")
async def list_change_requests(
    pagination: PaginationParams = Depends(pagination_params),
    status_filter: str | None = Query("pending", alias="status"),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Change-request queue across all vendors. Defaults to the pending
    queue; pass `?status=approved|rejected|all` to widen. List view masks
    the proposed value (last-4 only) — the full value is on the detail."""
    query = select(VendorChangeRequest)
    if status_filter and status_filter != "all":
        query = query.where(VendorChangeRequest.status == status_filter)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    query = (
        query.order_by(VendorChangeRequest.created_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    rows = (await db.execute(query)).scalars().all()

    items = []
    for r in rows:
        items.append(
            VendorChangeRequestResponse.from_db(
                r, vendor_name=await _vendor_name(db, r.vendor_id), reveal=False
            )
        )
    return paginated(items, total, pagination)


# ---------- Sanctions / risk screening ----------
#
# The literal `/screening/review-queue` route is declared BEFORE the parametric
# `/{vendor_id}` routes so "screening" isn't captured as a `vendor_id`. (The
# `{vendor_id}` converter is `uuid.UUID`, so "screening" would 422 rather than
# mismatch anyway — but declaration order makes the intent explicit and safe.)


@router.get("/screening/review-queue", response_model=list[ScreeningReviewItem])
async def screening_review_queue(
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
    entity_id: uuid.UUID | None = Depends(get_entity_id),
):
    """Vendors needing screening attention — `match` or `review` status —
    newest screen first. Each row carries the matched-list NAME + provider
    from its most recent `sanctions_checks` row (never raw match details)."""
    query = apply_entity_scope(
        select(Vendor).where(Vendor.screening_status.in_(("match", "review"))),
        Vendor,
        entity_id,
    )
    query = query.order_by(Vendor.last_screened_at.desc().nulls_last())
    vendors = (await db.execute(query)).scalars().all()

    items: list[ScreeningReviewItem] = []
    for v in vendors:
        latest = (
            await db.execute(
                select(SanctionsCheck)
                .where(SanctionsCheck.vendor_id == v.id)
                .order_by(SanctionsCheck.checked_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        items.append(
            ScreeningReviewItem(
                vendor_id=str(v.id),
                vendor_name=v.name,
                screening_status=v.screening_status,
                last_screened_at=v.last_screened_at.isoformat() if v.last_screened_at else None,
                payments_blocked=bool(v.payments_blocked),
                risk_level=getattr(v, "risk_level", "unknown") or "unknown",
                risk_score=(
                    str(v.risk_score) if getattr(v, "risk_score", None) is not None else None
                ),
                latest_matched_list=latest.matched_list if latest else None,
                latest_provider=latest.provider if latest else None,
            )
        )
    return items


@router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # SOX access-control auditing: record who VIEWED a vendor's regulated
    # fields. Log the field-NAMES surfaced, never the values (PII-out-of-logs).
    viewed_fields = [
        name
        for name, present in (("tax_id", vendor.tax_id), ("bank_details", vendor.bank_details))
        if present
    ]
    await log_access(
        db,
        user=user,
        organization_id=vendor.organization_id,
        entity_type="vendor",
        entity_id=vendor.id,
        fields=viewed_fields or None,
    )
    await db.commit()

    return VendorResponse.from_db(vendor)


@router.post("", response_model=VendorResponse, status_code=status.HTTP_201_CREATED)
async def create_vendor(
    body: VendorCreate,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # vendor.manage defaults to admin/ap_manager (unchanged) — splittable so an
    # org can grant master-data management without payment authority.
    user: User = Depends(require_permission(PERM_VENDOR_MANAGE)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    # Bank details on a brand-new vendor are dual-controlled exactly like an
    # update — creating a payable vendor with attacker-controlled bank details
    # was the single-person-action BEC bypass this closes (fake-new-payee is
    # the more common real-world pattern than a bank-redirect on an existing
    # vendor, which was already gated). Strip it off the initial insert; the
    # vendor row lands with NO bank details until a SECOND user approves the
    # staged VendorChangeRequest via the same flow as an existing-vendor
    # bank-details PATCH.
    payload = body.model_dump()
    incoming_bank_details = payload.pop("bank_details", None)

    vendor = Vendor(
        **payload,
        organization_id=org_id,
        entity_id=entity_id,
        status="active",
        source="manual",
        verified_by=user.full_name,
        verified_at=datetime.now(UTC),
    )
    db.add(vendor)
    await db.flush()
    await db.refresh(vendor)

    # Append-only audit row for the create (invariant #3 — vendor mutations
    # write an audit trail). PII guard: record whether tax_id / bank_details
    # were SUBMITTED, never their raw values — `has_bank_details` reflects the
    # request, not the row (which has none yet pending approval).
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.created",
        entity_type="vendor",
        entity_id=vendor.id,
        details={
            "name": vendor.name,
            "code": vendor.code,
            "has_tax_id": bool(vendor.tax_id),
            "has_bank_details": bool(incoming_bank_details),
        },
    )

    if incoming_bank_details:
        await _stage_ap_bank_change(
            db, vendor=vendor, incoming=incoming_bank_details, user=user, org_id=org_id
        )

    # Initial sanctions / PEP screen. Best-effort: a provider failure must
    # never roll back or 500 the vendor write, so the screen runs inside a
    # SAVEPOINT and its failure is swallowed (the vendor row survives).
    await _screen_best_effort(
        db, vendor=vendor, org=org, org_id=org_id, check_type="initial", actor_id=user.id
    )

    # Build the response AFTER screening so it reflects screening_status /
    # payments_blocked set by the screen.
    return VendorResponse.from_db(vendor)


@router.patch("/{vendor_id}", response_model=VendorResponse)
async def update_vendor(
    vendor_id: uuid.UUID,
    body: VendorUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_permission(PERM_VENDOR_MANAGE)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    payload = body.model_dump(exclude_unset=True)

    # Snapshot the pre-mutation state for the SOX change diff (taken BEFORE we
    # mutate the row). `bank_details` / `tax_id` are masked into the diff, so we
    # only need the raw before-values transiently in-process here.
    before_scalars = {f: getattr(vendor, f) for f in _AUDITABLE_SCALAR_FIELDS}
    before_tax_last4 = _last4(vendor.tax_id)

    # Bank-details edits are DUAL-CONTROL: stage a pending change request rather
    # than applying inline (the BEC / bank-redirect gate — see _stage_ap_bank_change).
    # A second approver must sign off via /change-requests/{id}/approve. The
    # canonical path is POST /{vendor_id}/bank-change; staging here too means a
    # stray PATCH carrying bank_details can neither silently apply nor silently
    # drop it. Other (non-bank) fields in the same PATCH still apply normally.
    if "bank_details" in payload:
        incoming = payload.pop("bank_details")
        await _stage_ap_bank_change(db, vendor=vendor, incoming=incoming, user=user, org_id=org_id)

    # Re-screen only when an identity-relevant field actually changed — a
    # name / tax_id / beneficial-owner edit can flip a vendor onto (or off of) a
    # sanctions list. Cosmetic edits (phone, terms) don't, and a bank change is
    # only STAGED here (not applied), so it doesn't re-screen the live vendor.
    identity_changed = bool((_IDENTITY_FIELDS - {"bank_details"}) & payload.keys())

    for field, value in payload.items():
        setattr(vendor, field, value)

    await db.flush()
    await db.refresh(vendor)

    # Append-only audit row with a field-level before/after diff (invariant #3).
    # PII guard: scalar fields go verbatim, but tax_id and bank_details record
    # masked last-4s only — the raw account number / tax id never enter the
    # audit trail. This is the row that catches an insider/BEC bank-redirect.
    after_scalars = {f: getattr(vendor, f) for f in _AUDITABLE_SCALAR_FIELDS}
    changes = build_field_diff(before_scalars, after_scalars, list(_AUDITABLE_SCALAR_FIELDS))
    if "tax_id" in payload:
        after_tax_last4 = _last4(vendor.tax_id)
        if after_tax_last4 != before_tax_last4:
            changes["tax_id"] = {"old_last4": before_tax_last4, "new_last4": after_tax_last4}

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.updated",
        entity_type="vendor",
        entity_id=vendor.id,
        details={"changes": changes},
    )

    if identity_changed:
        await _screen_best_effort(
            db, vendor=vendor, org=org, org_id=org_id, check_type="initial", actor_id=user.id
        )

    return VendorResponse.from_db(vendor)


@router.post(
    "/{vendor_id}/bank-change",
    response_model=VendorChangeRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def request_bank_change(
    vendor_id: uuid.UUID,
    body: VendorBankChangeRequest,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_permission(PERM_VENDOR_MANAGE)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Propose a vendor bank-details change (AP-initiated, dual-control).

    The canonical AP path for changing where a vendor is paid. It does NOT apply
    the change — it stages a pending VendorChangeRequest that a second user with
    `vendor.bank_change.approve` must approve (and who can't be the proposer).
    Returns the staged request (202). See docs/authentication.md § SoD.
    """
    vendor = (await db.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    req = await _stage_ap_bank_change(
        db, vendor=vendor, incoming=body.bank_details, user=user, org_id=org_id
    )
    await db.commit()
    await db.refresh(req)
    return VendorChangeRequestResponse.from_db(req, vendor_name=vendor.name, reveal=True)


@router.delete("/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    # Capture the PII-free identifiers before the row is gone — the audit row
    # below records name/code only, never tax_id / bank_details.
    vendor_name, vendor_code = vendor.name, vendor.code

    # A vendor is only hard-deletable when it carries no retained business or
    # compliance history. Destroying a transacted vendor must NOT be allowed:
    # it would erase records SOX retention requires and orphan downstream rows.
    # Such vendors are DEACTIVATED (PATCH status = "inactive"), not deleted, so
    # their history (and SOX audit trail) survives.
    #
    # We enforce this with an EXPLICIT pre-check rather than by relying on the
    # database FK, because the two failure modes are inconsistent and one is
    # silent:
    #   * Tables WITHOUT a `Vendor.<rel>` ORM relationship (sanctions_checks,
    #     priors, embeddings, credit_memos, contracts, POs, virtual_cards,
    #     discounts) raise a raw IntegrityError on delete — the 500 the worker
    #     hit, since every API-created vendor has a create-time screening row.
    #   * Tables WITH a relationship — `Vendor.invoices` (no cascade) — are
    #     worse: SQLAlchemy SILENTLY issues `UPDATE invoices SET vendor_id=NULL`
    #     and the delete "succeeds", orphaning the invoice. An FK/savepoint
    #     guard would never even see it.
    # Counting the retained business tables up front catches both deterministically.
    blocking: list[str] = []
    for model, label in (
        (Invoice, "invoices"),
        (CreditMemo, "credit memos"),
        (Contract, "contracts"),
        (PurchaseOrder, "purchase orders"),
        (VirtualCard, "virtual cards"),
        (DiscountOffer, "discount offers"),
    ):
        count = (
            await db.execute(
                select(func.count()).select_from(model).where(model.vendor_id == vendor_id)
            )
        ).scalar() or 0
        if count:
            blocking.append(f"{count} {label}")
    if blocking:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Vendor has retained history ({', '.join(blocking)}) and cannot be "
                "deleted. Deactivate it instead (set status to 'inactive')."
            ),
        )

    # No business history — clean up the vendor-OWNED derived rows that carry no
    # independent retention value for a never-transacted vendor (its screening
    # trail, extraction priors, embeddings; change-requests + portal users
    # already cascade), then delete the vendor.
    await db.execute(sa_delete(SanctionsCheck).where(SanctionsCheck.vendor_id == vendor_id))
    await db.execute(
        sa_delete(VendorExtractionPrior).where(VendorExtractionPrior.vendor_id == vendor_id)
    )
    await db.execute(sa_delete(InvoiceEmbedding).where(InvoiceEmbedding.vendor_id == vendor_id))
    await db.delete(vendor)
    await db.flush()

    # Delete succeeded — write the append-only audit row (invariant #3).
    # `audit_log.entity_id` is a bare UUID (no FK), so the row outlives the
    # vendor it references.
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.deleted",
        entity_type="vendor",
        entity_id=vendor_id,
        details={"name": vendor_name, "code": vendor_code},
    )
    await db.commit()


@router.post("/{vendor_id}/screen", response_model=VendorResponse)
async def screen_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Manually re-screen one vendor against the configured sanctions provider.

    Unlike the create/update screen, a manual re-screen is foreground: a
    provider failure surfaces as a 502 so the operator knows the screen did
    not run (rather than silently appearing 'clear')."""
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    try:
        await screen_vendor_record(
            db,
            vendor=vendor,
            organization_id=org_id,
            org_settings=org.settings,
            check_type="manual",
            actor_id=user.id,
        )
    except Exception as exc:  # noqa: BLE001
        # Log the exception type, never the message/traceback. A sanctions
        # adapter's error string could embed a vendor identifier; interpolating
        # `exc` (or exc_info=True) would push that into the log sink (invariant #7).
        logger.warning(
            "Manual sanctions screen failed for vendor=%s: %s",
            vendor.id,
            exc.__class__.__name__,
        )
        raise HTTPException(status_code=502, detail="Sanctions provider screening failed") from exc

    await db.commit()
    await db.refresh(vendor)
    return VendorResponse.from_db(vendor)


@router.get("/{vendor_id}/screening-history", response_model=list[SanctionsCheckResponse])
async def vendor_screening_history(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_AP_CLERK, ROLE_CFO)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """The append-only screening trail for one vendor, newest first (cap 100)."""
    await _get_vendor_or_404(db, vendor_id)

    rows = (
        (
            await db.execute(
                select(SanctionsCheck)
                .where(SanctionsCheck.vendor_id == vendor_id)
                .order_by(SanctionsCheck.checked_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )

    # SOX access auditing: record who VIEWED the screening trail (field-name
    # only, never the underlying match details).
    await log_access(
        db,
        user=user,
        organization_id=org_id,
        entity_type="vendor",
        entity_id=vendor_id,
        fields=["sanctions_checks"],
    )
    await db.commit()

    return [SanctionsCheckResponse.from_db(c) for c in rows]


@router.post("/{vendor_id}/block", response_model=VendorResponse)
async def block_vendor_payments(
    vendor_id: uuid.UUID,
    body: VendorBlockRequest | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_permission(PERM_VENDOR_BLOCK)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Manually block all payments to a vendor. The block is sticky —
    `check_payment_compliance` refuses every payment until an unblock."""
    vendor = await _get_vendor_or_404(db, vendor_id)
    reason = (body.reason if body else None) or "manually blocked by AP"

    vendor.payments_blocked = True
    vendor.payments_blocked_reason = reason
    vendor.payments_blocked_at = datetime.now(UTC)
    await db.flush()

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.payment_blocked",
        entity_type="vendor",
        entity_id=vendor.id,
        details={"reason": reason},
    )
    await db.commit()
    await db.refresh(vendor)
    return VendorResponse.from_db(vendor)


@router.post("/{vendor_id}/unblock", response_model=VendorResponse)
async def unblock_vendor_payments(
    vendor_id: uuid.UUID,
    body: VendorBlockRequest | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_permission(PERM_VENDOR_BLOCK)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Lift a payment block. Clears the block flag, reason, and timestamp."""
    vendor = await _get_vendor_or_404(db, vendor_id)
    reason = (body.reason if body else None) or "manually unblocked by AP"

    vendor.payments_blocked = False
    vendor.payments_blocked_reason = None
    vendor.payments_blocked_at = None
    await db.flush()

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.payment_unblocked",
        entity_type="vendor",
        entity_id=vendor.id,
        details={"reason": reason},
    )
    await db.commit()
    await db.refresh(vendor)
    return VendorResponse.from_db(vendor)


@router.post("/{vendor_id}/verify", response_model=VendorResponse)
async def verify_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_permission(PERM_VENDOR_MANAGE)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Verify an unverified vendor — makes them eligible for payment."""
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if vendor.status != "unverified":
        raise HTTPException(status_code=409, detail="Vendor is not in unverified status")

    vendor.status = "active"
    vendor.verified_by = user.full_name
    vendor.verified_at = datetime.now(UTC)
    await db.flush()

    # Append-only audit row — verification makes the vendor payment-eligible,
    # a regulated state change (invariant #3).
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.verified",
        entity_type="vendor",
        entity_id=vendor.id,
        details={"status": {"old": "unverified", "new": "active"}},
    )
    await db.commit()
    return VendorResponse.from_db(vendor)


@router.post("/{vendor_id}/reject", response_model=VendorResponse)
async def reject_vendor(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_permission(PERM_VENDOR_MANAGE)),
    org_id: uuid.UUID = Depends(get_org_id),
):
    """Reject an unverified vendor — marks as invalid/duplicate."""
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    if vendor.status not in ("unverified", "active"):
        raise HTTPException(status_code=409, detail="Vendor cannot be rejected from this status")

    prev_status = vendor.status
    vendor.status = "rejected"
    await db.flush()

    # Append-only audit row — rejection is a regulated state change (#3).
    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org_id,
        actor_id=user.id,
        action="vendor.rejected",
        entity_type="vendor",
        entity_id=vendor.id,
        details={"status": {"old": prev_status, "new": "rejected"}},
    )
    await db.commit()
    return VendorResponse.from_db(vendor)


@router.post("/sync-erp")
async def sync_vendors_from_erp_endpoint(
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    """Pull vendors from the connected ERP and sync to local database."""
    erp_config = (org.settings or {}).get("erp")
    if not erp_config:
        raise HTTPException(
            status_code=400,
            detail="No ERP configured. Set up ERP integration in Organization Settings.",
        )

    # Use ERP adapter to fetch vendors
    import app.services.erp_adapters.dynamics_365_bc  # noqa: F401
    import app.services.erp_adapters.merge_dev  # noqa: F401
    import app.services.erp_adapters.mock_adapter  # noqa: F401
    import app.services.erp_adapters.netsuite  # noqa: F401
    from app.services.erp_adapters import get_erp_adapter

    adapter = get_erp_adapter(erp_config)
    try:
        erp_vendors = await adapter.list_vendors()
    except Exception as exc:
        logger.exception("ERP list_vendors failed for org %s", org_id)
        raise HTTPException(
            status_code=502,
            detail=f"ERP request failed: {type(exc).__name__}",
        ) from exc

    vendor_dicts = [dataclasses.asdict(v) for v in erp_vendors]

    result = await sync_vendors_from_erp(db, org_id, vendor_dicts, entity_id=entity_id)
    await db.commit()

    return {
        "success": True,
        "message": (
            f"Synced {result['created']} new, "
            f"{result['updated']} updated, "
            f"{result['unchanged']} unchanged"
        ),
        **result,
    }


@router.post("/import-csv", status_code=status.HTTP_200_OK)
async def import_vendors_from_csv(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
    org_id: uuid.UUID = Depends(get_org_id),
    entity_id: uuid.UUID = Depends(get_write_entity_id),
):
    """Bulk-create vendors from a CSV export.

    Columns (case-insensitive, order-free): ``name`` (required), ``code``,
    ``email``, ``phone``, ``address``, ``tax_id``, ``payment_terms``,
    ``accepts_virtual_cards``. Duplicate detection uses ``code`` first, then
    case-insensitive ``name``. See ``backend/docs/csv-import.md``.
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

    result = await import_vendors_csv(db, org_id, csv_text, entity_id=entity_id)
    await db.commit()
    return result.to_dict()


# ---------- Supplier-portal user management ----------


def _vendor_user_response(vu: VendorUser) -> PortalUserResponse:
    return PortalUserResponse(
        id=str(vu.id),
        vendor_id=str(vu.vendor_id),
        email=vu.email,
        full_name=vu.full_name,
        is_active=vu.is_active,
        must_change_password=vu.must_change_password,
        last_login_at=vu.last_login_at,
        created_at=vu.created_at,
    )


async def _get_vendor_or_404(db: AsyncSession, vendor_id: uuid.UUID) -> Vendor:
    result = await db.execute(select(Vendor).where(Vendor.id == vendor_id))
    vendor = result.scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")
    return vendor


@router.get("/{vendor_id}/portal-users", response_model=list[PortalUserResponse])
async def list_vendor_portal_users(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    await _get_vendor_or_404(db, vendor_id)
    rows = (
        (
            await db.execute(
                select(VendorUser)
                .where(VendorUser.vendor_id == vendor_id)
                .order_by(VendorUser.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_vendor_user_response(vu) for vu in rows]


@router.post(
    "/{vendor_id}/portal-users",
    response_model=PortalInviteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_vendor_portal_user(
    vendor_id: uuid.UUID,
    body: PortalInviteRequest,
    org: Organization = Depends(get_tenant),
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Create a supplier-portal user for a vendor and email them a temp
    password. Idempotent-ish: second invite for the same email is rejected
    (409) so we don't silently overwrite a working credential."""
    vendor = await _get_vendor_or_404(db, vendor_id)

    existing = (
        await db.execute(select(VendorUser).where(VendorUser.email == body.email))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="A portal user with this email already exists.")

    temp_password = generate_temp_password()
    vu = VendorUser(
        vendor_id=vendor.id,
        organization_id=vendor.organization_id,
        email=body.email,
        full_name=body.full_name,
        hashed_password=_pwd.hash(temp_password),
        is_active=True,
        must_change_password=True,
    )
    db.add(vu)
    await db.flush()
    await db.refresh(vu)

    # Best-effort welcome email. If delivery fails we still return 201 with
    # `temp_password` so the admin can share it manually — same pattern as
    # the tenant-signup welcome email.
    email_adapter = get_email_adapter()
    portal_url = f"https://{org.slug}.app.com/portal"
    try:
        await email_adapter.send(
            EmailMessage(
                to=body.email,
                subject=f"You've been invited to {org.name}'s supplier portal",
                body_text=(
                    f"Hi {body.full_name},\n\n"
                    f"{org.name} has set up a supplier-portal account for "
                    f"{vendor.name}. Use it to submit invoices and track "
                    f"payment status.\n\n"
                    f"  URL:      {portal_url}\n"
                    f"  Email:    {body.email}\n"
                    f"  Password: {temp_password}\n\n"
                    "You'll be asked to change your password on first sign-in.\n"
                ),
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "Portal-user welcome email failed for %s (vendor=%s)", body.email, vendor.id
        )

    return PortalInviteResponse(user=_vendor_user_response(vu), temp_password=temp_password)


@router.delete(
    "/{vendor_id}/portal-users/{vendor_user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_vendor_portal_user(
    vendor_id: uuid.UUID,
    vendor_user_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    result = await db.execute(
        select(VendorUser).where(
            VendorUser.id == vendor_user_id,
            VendorUser.vendor_id == vendor_id,
        )
    )
    vu = result.scalar_one_or_none()
    if not vu:
        raise HTTPException(status_code=404, detail="Portal user not found")
    await db.delete(vu)
    await db.commit()


# ---------- Vendor change-request approval (fraud-prevention gate) ----------
#
# Bank-detail and tax-ID changes initiated by a supplier-portal user stage a
# pending `vendor_change_requests` row instead of mutating the vendor. AP
# approval is what applies the change — until then a redirected bank account
# has no effect on where money goes. Every approve/reject is a status change,
# so it writes an append-only audit row (invariant #3).


@router.get("/{vendor_id}/change-requests", response_model=list[VendorChangeRequestResponse])
async def list_vendor_change_requests(
    vendor_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER, ROLE_CFO)),
):
    """Change requests for one vendor. Reveals the full proposed value so
    AP can verify the new bank / tax details before approving."""
    rows = (
        (
            await db.execute(
                select(VendorChangeRequest)
                .where(VendorChangeRequest.vendor_id == vendor_id)
                .order_by(VendorChangeRequest.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    vname = await _vendor_name(db, vendor_id)
    return [VendorChangeRequestResponse.from_db(r, vendor_name=vname, reveal=True) for r in rows]


@router.post("/change-requests/{request_id}/approve", response_model=VendorChangeRequestResponse)
async def approve_change_request(
    request_id: uuid.UUID,
    body: VendorChangeReviewRequest | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    # The BEC / bank-redirect fraud gate. SoD-splittable from payment.execute so
    # the person who can redirect where money goes can't also send it. Defaults
    # to admin/ap_manager (unchanged).
    user: User = Depends(require_permission(PERM_VENDOR_BANK_CHANGE_APPROVE)),
):
    """Apply the staged change to the vendor and mark the request approved.

    The request row is locked `FOR UPDATE` so two concurrent approvals can't
    both apply the change. Re-approving an already-resolved request is a
    409 — the lock + status check makes the apply exactly-once.
    """
    req = (
        await db.execute(
            select(VendorChangeRequest)
            .where(VendorChangeRequest.id == request_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Change request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="Change request already resolved")
    # Segregation of duties: the AP user who PROPOSED a change can't be the one
    # who approves it (dual control). Portal-submitted requests have no AP
    # requester, so this only bites AP-initiated ones.
    if req.requested_by_user_id is not None and req.requested_by_user_id == user.id:
        raise HTTPException(
            status_code=403,
            detail="You cannot approve a bank-detail change you requested.",
        )

    vendor = (
        await db.execute(select(Vendor).where(Vendor.id == req.vendor_id))
    ).scalar_one_or_none()
    if not vendor:
        raise HTTPException(status_code=404, detail="Vendor not found")

    last4: str | None = None
    if req.change_type == "bank_details":
        incoming = (req.proposed_value or {}).get("bank_details") or {}
        account = str(incoming.get("account_number") or "")
        last4 = account[-4:] if len(account) >= 4 else None
        vendor.bank_details = _merge_bank_details(vendor.bank_details, incoming)
    elif req.change_type == "tax_id":
        new_tax = str((req.proposed_value or {}).get("tax_id") or "")
        last4 = new_tax[-4:] if len(new_tax) >= 4 else None
        vendor.tax_id = new_tax
        # A re-keyed tax ID invalidates any prior TIN verification.
        vendor.tin_verified_at = None
    else:
        raise HTTPException(status_code=400, detail="Unknown change type")

    req.status = "approved"
    req.reviewed_by_user_id = user.id
    req.reviewed_at = datetime.now(UTC)
    if body and body.review_note:
        req.review_note = body.review_note
    await db.flush()

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action=f"vendor.{req.change_type}_change_approved",
        entity_type="vendor",
        entity_id=vendor.id,
        # PII guard: never log the value — only a last-4 + the request id.
        details={"request_id": str(req.id), "change_type": req.change_type, "last4": last4},
    )

    # BEC gate: a bank-detail change silently re-points where money goes. After
    # applying it, (a) re-screen the vendor against sanctions/KYC with the new
    # coordinates, and (b) raise a fraud_flag exception on every invoice already
    # in the payment queue for this vendor — so the next payment run gets a human
    # second look before money leaves. Without this the queue shows nothing and a
    # phished/insider approval lands the redirect with no operational signal.
    if req.change_type == "bank_details":
        await _screen_best_effort(
            db,
            vendor=vendor,
            org=org,
            org_id=org.id,
            check_type="bank_change",
            actor_id=user.id,
        )
        await _flag_payable_invoices_for_bank_change(db, vendor=vendor)

    await db.commit()
    await db.refresh(req)
    return VendorChangeRequestResponse.from_db(req, vendor_name=vendor.name, reveal=True)


@router.post("/change-requests/{request_id}/reject", response_model=VendorChangeRequestResponse)
async def reject_change_request(
    request_id: uuid.UUID,
    body: VendorChangeReviewRequest | None = None,
    db: AsyncSession = Depends(get_tenant_db),
    org: Organization = Depends(get_tenant),
    user: User = Depends(require_roles(ROLE_ADMIN, ROLE_AP_MANAGER)),
):
    """Mark the request rejected without ever touching the vendor row."""
    req = (
        await db.execute(
            select(VendorChangeRequest)
            .where(VendorChangeRequest.id == request_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if not req:
        raise HTTPException(status_code=404, detail="Change request not found")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="Change request already resolved")

    req.status = "rejected"
    req.reviewed_by_user_id = user.id
    req.reviewed_at = datetime.now(UTC)
    if body and body.review_note:
        req.review_note = body.review_note
    await db.flush()

    await dispatch_audit(
        db,
        correlation_id=uuid.uuid4(),
        organization_id=org.id,
        actor_id=user.id,
        action=f"vendor.{req.change_type}_change_rejected",
        entity_type="vendor",
        entity_id=req.vendor_id,
        details={"request_id": str(req.id), "change_type": req.change_type},
    )
    await db.commit()
    await db.refresh(req)
    return VendorChangeRequestResponse.from_db(
        req, vendor_name=await _vendor_name(db, req.vendor_id), reveal=True
    )
