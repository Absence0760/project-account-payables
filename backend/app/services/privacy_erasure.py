"""Right-to-erasure / anonymization (GDPR Art. 17 / CCPA right-to-delete).

Irreversibly redact a data subject's PII **while preserving the immutable
financial + audit record**. Legally-required retention (tax, SOX, AP records)
wins over erasure for transactional rows: we redact the PII *text* fields and
keep the money trail (amounts, statuses, dates) and the append-only
``audit_log`` completely intact.

What is redacted vs. preserved, per subject type:

  * ``user`` (control plane) — redact ``email`` / ``full_name`` / ``sso_*``,
    null the MFA secret + password, deactivate. **Preserve** the row id,
    ``organization_id``, role assignments, and every ``audit_log`` row the user
    authored (the actor_id link stays — non-repudiation).
  * ``vendor_user`` (tenant) — redact ``email`` / ``full_name``, null
    ``hashed_password`` + MFA secret, deactivate.
  * ``vendor_contact`` (tenant Vendor) — redact contact PII (``email`` /
    ``phone`` / ``address`` / ``tax_id`` / ``bank_details`` /
    ``beneficial_owner_data``). **Preserve** ``vendor.name`` (the legal payee on
    every invoice's ``vendor_name`` money field) and every related Invoice /
    Payment amount + status. Supplier-chat message *bodies* the supplier wrote
    are also redacted (free-text PII) but the thread + audit timeline stay.

Hard guarantees (project invariants):
  * **No money field is ever touched** — only PII text columns are nulled /
    tombstoned. Amounts, statuses, currencies, dates are untouched.
  * **``audit_log`` is append-only** — erasure NEVER updates or deletes an audit
    row; it writes a NEW one (done by the caller via ``dispatch_audit``).
  * **Idempotent** — re-running erasure on an already-erased subject is a safe
    no-op (detected via the ``erased_at`` tombstone marker).

Pure-ish: these mutate the passed ORM objects / session but never commit — the
API layer owns the transaction, the audit write, and the request-row insert.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_subject_request import (
    SUBJECT_USER,
    SUBJECT_VENDOR_CONTACT,
    SUBJECT_VENDOR_USER,
)
from app.models.supplier_chat import ChatAuthorRole, SupplierChatMessage, SupplierChatThread
from app.models.user import User
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser

# Redaction tombstones. Distinct, recognisable, and PII-free. The id suffix
# keeps formerly-unique columns unique after redaction (email has a UNIQUE
# constraint) and lets an operator correlate the row to its erasure request
# without revealing the original value.
REDACTED = "[redacted]"


def _redacted_email(subject_id: uuid.UUID) -> str:
    # Stays unique (email is UNIQUE on both User + VendorUser) and obviously
    # non-deliverable so it can never be used to re-contact the subject.
    return f"erased+{subject_id}@redacted.invalid"


class ErasureResult:
    """The non-PII outcome of an erasure run."""

    def __init__(self) -> None:
        self.already_erased: bool = False
        self.fields_redacted: int = 0
        self.record_counts: dict[str, int] = {}


async def erase_user(
    *,
    subject_id: uuid.UUID,
    organization_id: uuid.UUID,
    control_db: AsyncSession,
    now: datetime | None = None,
) -> ErasureResult:
    now = now or datetime.now(UTC)
    result = ErasureResult()
    user = (
        await control_db.execute(
            select(User).where(User.id == subject_id, User.organization_id == organization_id)
        )
    ).scalar_one_or_none()
    if user is None:
        # Already gone — treat as a completed no-op (idempotent).
        result.already_erased = True
        return result

    # Idempotency: the User model has no `meta` column, so we detect a prior
    # erasure by the tombstone email we wrote last time.
    if user.email.startswith("erased+") and user.email.endswith("@redacted.invalid"):
        result.already_erased = True
        return result

    user.email = _redacted_email(subject_id)
    user.full_name = REDACTED
    user.sso_provider = None
    user.sso_provider_id = None
    user.hashed_password = None
    user.mfa_secret = None
    user.mfa_enabled = False
    user.is_active = False
    result.fields_redacted = 6
    result.record_counts = {"users": 1}
    return result


async def erase_vendor_user(
    *,
    subject_id: uuid.UUID,
    tenant_db: AsyncSession,
    now: datetime | None = None,
) -> ErasureResult:
    now = now or datetime.now(UTC)
    result = ErasureResult()
    vu = (
        await tenant_db.execute(select(VendorUser).where(VendorUser.id == subject_id))
    ).scalar_one_or_none()
    if vu is None:
        result.already_erased = True
        return result

    if vu.email.startswith("erased+") and vu.email.endswith("@redacted.invalid"):
        result.already_erased = True
        return result

    vu.email = _redacted_email(subject_id)
    vu.full_name = REDACTED
    vu.hashed_password = None
    vu.mfa_secret = None
    vu.mfa_enabled = False
    vu.is_active = False
    result.fields_redacted = 5
    result.record_counts = {"vendor_users": 1}
    return result


async def erase_vendor_contact(
    *,
    subject_id: uuid.UUID,
    organization_id: uuid.UUID,
    tenant_db: AsyncSession,
    now: datetime | None = None,
) -> ErasureResult:
    """Redact a vendor's contact PII; preserve the legal payee + money trail.

    ``vendor.name`` is preserved — it's denormalised onto every Invoice's
    ``vendor_name`` (a record we must legally retain), and nulling it would
    orphan the financial trail. Only the *contact* fields a data subject can
    demand erased are redacted. We also redact the supplier-authored chat
    message bodies (free-text PII) but keep the thread + the AP side.

    The Vendor model has no generic ``meta`` JSONB, so a prior run is detected
    by whether the contact fields are already all NULL (and no portal user / chat
    body still needs redacting).
    """
    now = now or datetime.now(UTC)
    result = ErasureResult()
    vendor = (
        await tenant_db.execute(
            select(Vendor).where(Vendor.id == subject_id, Vendor.organization_id == organization_id)
        )
    ).scalar_one_or_none()
    if vendor is None:
        result.already_erased = True
        return result

    # Idempotency: if every contact PII field is already cleared, this is a
    # re-run — no-op.
    contact_fields_cleared = (
        vendor.email is None
        and vendor.phone is None
        and vendor.address is None
        and vendor.tax_id is None
        and vendor.bank_details is None
        and vendor.beneficial_owner_data is None
    )

    fields_redacted = 0
    if not contact_fields_cleared:
        if vendor.email is not None:
            vendor.email = None
            fields_redacted += 1
        if vendor.phone is not None:
            vendor.phone = None
            fields_redacted += 1
        if vendor.address is not None:
            vendor.address = None
            fields_redacted += 1
        if vendor.tax_id is not None:
            vendor.tax_id = None
            fields_redacted += 1
        if vendor.bank_details is not None:
            vendor.bank_details = None
            fields_redacted += 1
        if vendor.beneficial_owner_data is not None:
            vendor.beneficial_owner_data = None
            fields_redacted += 1

    # Redact any portal users for this vendor too (their email/name is the same
    # natural person's PII).
    portal_users = (
        (await tenant_db.execute(select(VendorUser).where(VendorUser.vendor_id == subject_id)))
        .scalars()
        .all()
    )
    portal_redacted = 0
    for vu in portal_users:
        if vu.email.startswith("erased+") and vu.email.endswith("@redacted.invalid"):
            continue
        vu.email = _redacted_email(vu.id)
        vu.full_name = REDACTED
        vu.hashed_password = None
        vu.mfa_secret = None
        vu.mfa_enabled = False
        vu.is_active = False
        portal_redacted += 1

    # Redact supplier-authored chat message bodies (free-text PII the supplier
    # wrote). Keep the row + author_role so the AP timeline stays coherent.
    # Narrow to this vendor's invoices: thread.invoice -> Invoice.vendor_id.
    from app.models.invoice import Invoice

    vendor_invoice_ids = (
        (
            await tenant_db.execute(
                select(Invoice.id).where(
                    Invoice.vendor_id == subject_id, Invoice.organization_id == organization_id
                )
            )
        )
        .scalars()
        .all()
    )
    chat_redacted = 0
    if vendor_invoice_ids:
        msgs = (
            (
                await tenant_db.execute(
                    select(SupplierChatMessage)
                    .join(
                        SupplierChatThread, SupplierChatMessage.thread_id == SupplierChatThread.id
                    )
                    .where(
                        SupplierChatThread.invoice_id.in_(vendor_invoice_ids),
                        SupplierChatMessage.author_role == ChatAuthorRole.supplier,
                    )
                )
            )
            .scalars()
            .all()
        )
        for m in msgs:
            if m.body != REDACTED:
                m.body = REDACTED
                m.author_name = None
                chat_redacted += 1

    if contact_fields_cleared and portal_redacted == 0 and chat_redacted == 0:
        result.already_erased = True
        return result

    result.fields_redacted = fields_redacted
    result.record_counts = {
        "vendors": 1,
        "vendor_contact_fields": fields_redacted,
        "portal_users_redacted": portal_redacted,
        "chat_messages_redacted": chat_redacted,
    }
    return result


async def erase_subject(
    *,
    subject_type: str,
    subject_id: uuid.UUID,
    organization_id: uuid.UUID,
    control_db: AsyncSession,
    tenant_db: AsyncSession,
    now: datetime | None = None,
) -> ErasureResult:
    """Dispatch erasure to the per-subject-type redactor.

    Never commits and never touches a money field or an ``audit_log`` row.
    """
    if subject_type == SUBJECT_USER:
        return await erase_user(
            subject_id=subject_id,
            organization_id=organization_id,
            control_db=control_db,
            now=now,
        )
    if subject_type == SUBJECT_VENDOR_USER:
        return await erase_vendor_user(subject_id=subject_id, tenant_db=tenant_db, now=now)
    if subject_type == SUBJECT_VENDOR_CONTACT:
        return await erase_vendor_contact(
            subject_id=subject_id,
            organization_id=organization_id,
            tenant_db=tenant_db,
            now=now,
        )
    raise ValueError(f"Unknown subject_type: {subject_type}")
