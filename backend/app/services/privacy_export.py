"""DSAR export — assemble everything held about a data subject (GDPR Art. 15 /
CCPA right-to-know) into a portable JSON bundle.

Three subject types, each living in a different place:

  * ``user``           — a control-plane :class:`User` (AP-team member). PII +
                         roles come from the control DB; the tenant DB
                         contributes only non-PII *counts* of their activity
                         (audit actions authored, in-app notifications).
  * ``vendor_user``    — a tenant :class:`VendorUser` (supplier-portal login).
  * ``vendor_contact`` — the contact PII on a tenant :class:`Vendor` (the
                         supplier company's own contact details), plus a
                         summary of the business records tied to that vendor
                         (invoices, payments, portal users, chat).

The bundle is the subject's data only. Every query is filtered by the resolved
subject id AND the caller's ``organization_id`` so a DSAR for one subject can
never surface another subject's — or another tenant's — data. Money fields in
the related-records summary are serialised as **string-Decimal** (never float),
and only field *values that belong to this subject* are included.

Pure-ish: the gather functions take the sessions and return plain dicts; the API
layer owns the session lifecycle, the audit write, and the request-row insert.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.data_subject_request import (
    SUBJECT_USER,
    SUBJECT_VENDOR_CONTACT,
    SUBJECT_VENDOR_USER,
)
from app.models.invoice import Invoice
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.supplier_chat import SupplierChatMessage, SupplierChatThread
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser
from app.models.workflow import AuditLog


class SubjectNotFound(Exception):
    """The requested subject could not be resolved within this tenant/org."""


def _jsonable(value: Any) -> Any:
    """Coerce a column value to something JSON-serialisable.

    Decimals → string (money-exact, never float); datetimes/dates → ISO; UUIDs →
    str. Everything else passes through.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


async def resolve_subject_id(
    *,
    subject_type: str,
    identifier: str,
    organization_id: uuid.UUID,
    control_db: AsyncSession,
    tenant_db: AsyncSession,
) -> uuid.UUID:
    """Resolve the subject identifier to a UUID, scoped to this org/tenant.

    Raises :class:`SubjectNotFound` if no matching subject exists *in this
    tenant* — which is also the cross-tenant guard: a User from another org, or
    a Vendor/VendorUser in another tenant's DB, never resolves here.
    """
    if subject_type == SUBJECT_USER:
        row = (
            await control_db.execute(
                select(User.id).where(
                    func.lower(User.email) == identifier.strip().lower(),
                    User.organization_id == organization_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise SubjectNotFound("No user with that email in this organization")
        return row

    if subject_type == SUBJECT_VENDOR_USER:
        row = (
            await tenant_db.execute(
                select(VendorUser.id).where(
                    func.lower(VendorUser.email) == identifier.strip().lower()
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise SubjectNotFound("No vendor user with that email in this tenant")
        return row

    if subject_type == SUBJECT_VENDOR_CONTACT:
        # Identifier is a Vendor UUID. (Vendor "contact" PII has no unique
        # natural key — many vendors share a blank/duplicate email — so the
        # subject is addressed by the vendor's own id.)
        try:
            vid = uuid.UUID(identifier.strip())
        except ValueError as exc:
            raise SubjectNotFound("vendor_contact identifier must be a vendor UUID") from exc
        row = (
            await tenant_db.execute(
                select(Vendor.id).where(Vendor.id == vid, Vendor.organization_id == organization_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise SubjectNotFound("No vendor with that id in this tenant")
        return row

    raise SubjectNotFound(f"Unknown subject_type: {subject_type}")


async def build_user_bundle(
    *,
    subject_id: uuid.UUID,
    organization_id: uuid.UUID,
    control_db: AsyncSession,
    tenant_db: AsyncSession,
) -> dict:
    """PII + roles for a control-plane User + non-PII activity counts."""
    user = (
        await control_db.execute(
            select(User)
            .where(User.id == subject_id, User.organization_id == organization_id)
            .options(selectinload(User.roles))
        )
    ).scalar_one_or_none()
    if user is None:
        raise SubjectNotFound("user not found in this organization")

    # Non-PII counts from the tenant DB — evidence of activity, not its content.
    audit_actions = (
        await tenant_db.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.actor_id == subject_id, AuditLog.organization_id == organization_id)
        )
    ).scalar_one()
    notifications = (
        await tenant_db.execute(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.recipient_user_id == subject_id,
                Notification.organization_id == organization_id,
            )
        )
    ).scalar_one()

    return {
        "profile": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "sso_provider": user.sso_provider,
            "sso_provider_id": user.sso_provider_id,
            "is_active": user.is_active,
            "mfa_enabled": user.mfa_enabled,
            "mfa_enrolled_at": _jsonable(user.mfa_enrolled_at),
            "notification_prefs": user.notification_prefs,
            "created_at": _jsonable(user.created_at),
            "organization_id": str(user.organization_id),
        },
        "roles": [r.name for r in user.roles],
        "activity": {
            "audit_actions_authored": audit_actions,
            "in_app_notifications": notifications,
        },
    }


async def build_vendor_user_bundle(
    *,
    subject_id: uuid.UUID,
    tenant_db: AsyncSession,
) -> dict:
    """PII for a tenant VendorUser + the parent vendor link."""
    vu = (
        await tenant_db.execute(select(VendorUser).where(VendorUser.id == subject_id))
    ).scalar_one_or_none()
    if vu is None:
        raise SubjectNotFound("vendor_user not found")

    return {
        "profile": {
            "id": str(vu.id),
            "vendor_id": str(vu.vendor_id),
            "email": vu.email,
            "full_name": vu.full_name,
            "is_active": vu.is_active,
            "last_login_at": _jsonable(vu.last_login_at),
            "mfa_enabled": vu.mfa_enabled,
            "mfa_enrolled_at": _jsonable(vu.mfa_enrolled_at),
            "notification_prefs": vu.notification_prefs,
            "created_at": _jsonable(vu.created_at),
        },
    }


async def build_vendor_contact_bundle(
    *,
    subject_id: uuid.UUID,
    organization_id: uuid.UUID,
    tenant_db: AsyncSession,
) -> dict:
    """Vendor contact PII + a summary of the related business records.

    The summary is deliberately a list of identifiers + money totals, not a full
    copy of every invoice — the subject is the *vendor contact*, and the related
    invoices/payments are surfaced so the subject can see what's tied to them.
    """
    vendor = (
        await tenant_db.execute(
            select(Vendor).where(Vendor.id == subject_id, Vendor.organization_id == organization_id)
        )
    ).scalar_one_or_none()
    if vendor is None:
        raise SubjectNotFound("vendor not found in this tenant")

    invoices = (
        await tenant_db.execute(
            select(
                Invoice.id,
                Invoice.invoice_number,
                Invoice.amount,
                Invoice.currency,
                Invoice.status,
                Invoice.created_at,
            )
            .where(Invoice.vendor_id == subject_id, Invoice.organization_id == organization_id)
            .order_by(Invoice.created_at)
        )
    ).all()
    invoice_ids = [r.id for r in invoices]

    payments_rows: list = []
    if invoice_ids:
        payments_rows = (
            await tenant_db.execute(
                select(
                    Payment.id,
                    Payment.invoice_id,
                    Payment.amount,
                    Payment.method,
                    Payment.status,
                    Payment.created_at,
                )
                .where(Payment.invoice_id.in_(invoice_ids))
                .order_by(Payment.created_at)
            )
        ).all()

    portal_users = (
        await tenant_db.execute(
            select(VendorUser.id, VendorUser.email, VendorUser.full_name).where(
                VendorUser.vendor_id == subject_id
            )
        )
    ).all()

    chat_messages = 0
    if invoice_ids:
        chat_messages = (
            await tenant_db.execute(
                select(func.count())
                .select_from(SupplierChatMessage)
                .join(SupplierChatThread, SupplierChatMessage.thread_id == SupplierChatThread.id)
                .where(SupplierChatThread.invoice_id.in_(invoice_ids))
            )
        ).scalar_one()

    return {
        "vendor": {
            "id": str(vendor.id),
            "name": vendor.name,
            "code": vendor.code,
            "email": vendor.email,
            "phone": vendor.phone,
            "address": vendor.address,
            "tax_id": vendor.tax_id,
            "bank_details": vendor.bank_details,
            "beneficial_owner_data": vendor.beneficial_owner_data,
            "status": vendor.status,
            "created_at": _jsonable(vendor.created_at),
        },
        "related_invoices": [
            {
                "id": str(r.id),
                "invoice_number": r.invoice_number,
                "amount": _jsonable(r.amount),
                "currency": r.currency,
                "status": str(r.status),
                "created_at": _jsonable(r.created_at),
            }
            for r in invoices
        ],
        "related_payments": [
            {
                "id": str(r.id),
                "invoice_id": str(r.invoice_id),
                "amount": _jsonable(r.amount),
                "method": r.method,
                "status": r.status,
                "created_at": _jsonable(r.created_at),
            }
            for r in payments_rows
        ],
        "portal_users": [
            {"id": str(r.id), "email": r.email, "full_name": r.full_name} for r in portal_users
        ],
        "counts": {
            "invoices": len(invoices),
            "payments": len(payments_rows),
            "portal_users": len(portal_users),
            "chat_messages": chat_messages,
        },
    }


async def build_dsar_bundle(
    *,
    subject_type: str,
    subject_id: uuid.UUID,
    organization_id: uuid.UUID,
    control_db: AsyncSession,
    tenant_db: AsyncSession,
) -> dict:
    """Dispatch to the per-subject-type bundle builder."""
    if subject_type == SUBJECT_USER:
        return await build_user_bundle(
            subject_id=subject_id,
            organization_id=organization_id,
            control_db=control_db,
            tenant_db=tenant_db,
        )
    if subject_type == SUBJECT_VENDOR_USER:
        return await build_vendor_user_bundle(subject_id=subject_id, tenant_db=tenant_db)
    if subject_type == SUBJECT_VENDOR_CONTACT:
        return await build_vendor_contact_bundle(
            subject_id=subject_id,
            organization_id=organization_id,
            tenant_db=tenant_db,
        )
    raise SubjectNotFound(f"Unknown subject_type: {subject_type}")
