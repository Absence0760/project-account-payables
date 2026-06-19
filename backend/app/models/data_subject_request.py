"""Data-subject request (GDPR / CCPA) — `data_subject_requests`, tenant-scoped.

Records each privacy request the AP team services for a data subject — a DSAR
export (Article 15 / CCPA right-to-know) or an erasure / anonymization (Article
17 / CCPA right-to-delete). One row per request, written by the admin-only
``/api/privacy`` endpoints.

**Strictly PII-free.** A DSAR trail that itself stored the subject's email /
tax-id / bank details would defeat the purpose, so this table records only:

  * ``subject_type``      — ``user`` | ``vendor_user`` | ``vendor_contact``
  * ``subject_id``        — the resolved subject's UUID (a User / VendorUser /
                            Vendor id — an opaque key, not PII)
  * ``request_type``      — ``dsar_export`` | ``erasure``
  * ``status``            — ``completed`` (synchronous) / ``failed``
  * ``requested_by``      — the admin User id who ran it
  * counts / timestamps   — non-identifying processing metadata

The subject's actual data is never persisted here; the DSAR export bundle is
returned in the HTTP response and not stored. The append-only ``audit_log`` trail
(``privacy.dsar_export`` / ``privacy.erasure``) is the immutable record; this
table is the queryable request history for the privacy officer.

See ``backend/docs/privacy.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin

# Subject types — where the subject's PII lives.
SUBJECT_USER = "user"  # control-plane User (AP-team member)
SUBJECT_VENDOR_USER = "vendor_user"  # tenant VendorUser (supplier-portal login)
SUBJECT_VENDOR_CONTACT = "vendor_contact"  # tenant Vendor contact fields
SUBJECT_TYPES = (SUBJECT_USER, SUBJECT_VENDOR_USER, SUBJECT_VENDOR_CONTACT)

# Request types.
REQUEST_DSAR_EXPORT = "dsar_export"
REQUEST_ERASURE = "erasure"
REQUEST_TYPES = (REQUEST_DSAR_EXPORT, REQUEST_ERASURE)

# Request status.
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_NOOP = "noop"  # erasure of an already-erased subject — safe no-op
REQUEST_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_NOOP)


class DataSubjectRequest(Base, EntityMixin, TimestampMixin):
    __tablename__ = "data_subject_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    # What kind of request, against which subject. `subject_id` is the resolved
    # subject's UUID (User / VendorUser / Vendor id) — an opaque reference, NOT
    # PII. Nullable so a request that failed to resolve a subject is still
    # recorded.
    request_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    subject_type: Mapped[str] = mapped_column(String(20), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATUS_COMPLETED)

    # The admin who ran the request (control-plane User id).
    requested_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Non-identifying processing metadata: e.g. {"records_redacted": 3,
    # "related_invoices": 12}. NEVER any field value — names / counts only.
    record_counts: Mapped[dict | None] = mapped_column(JSONB)
    # Free-text note from the operator (e.g. legal basis); kept short + PII-free
    # by convention, never auto-populated from subject data.
    note: Mapped[str | None] = mapped_column(String(500))

    # Denormalised count of redacted PII fields, for the list view.
    fields_redacted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
