"""PEPPOL transmission log: peppol_transmissions table (tenant-scoped).

One row per attempt to transmit an invoice over the PEPPOL network. Tenant-
scoped (``EntityMixin`` + ``TimestampMixin`` + explicit ``organization_id``),
mirroring ``agent_decision.py`` / ``exception.py``. NOT in
``tenant_provisioning.CONTROL_TABLES``, so it fans out to every tenant DB.

Idempotency is enforced at the DATA layer, not by application code: a PARTIAL
unique index over *live* (non-failed) transmissions guarantees at most one
non-failed outbound transmission per invoice. A second concurrent send raises
``IntegrityError``; a prior *failed* send is excluded from the index, so a
retry after a failure is allowed.

Inbound-ready: the ``direction`` column (defaults ``outbound``) and the
partial-unique ``message_id`` column let the next (inbound) slice dedupe
redeliveries by the AS4 MessageId — the same shape payment webhooks dedupe by
``event_id``.

PII invariant: ``participant_value`` / ``sender_value`` hold org / tax ids that
live legitimately on this row and inside the UBL payload, but NEVER enter a log
line or an HTTP error body. Money (``amount``) is ``Numeric(15, 2)`` — never
float.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin


class PeppolTransmission(Base, EntityMixin, TimestampMixin):
    __tablename__ = "peppol_transmissions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False, index=True
    )
    # "outbound" | "inbound" — this slice only writes "outbound".
    direction: Mapped[str] = mapped_column(String(10), nullable=False, default="outbound")
    # The COUNTERPARTY (receiver for outbound) participant id — scheme + value.
    # `participant_value` is the supplier's org/tax id (PII — never logged).
    participant_scheme: Mapped[str] = mapped_column(String(20), nullable=False)
    participant_value: Mapped[str] = mapped_column(String(100), nullable=False)
    # Our (C1) participant id.
    sender_scheme: Mapped[str | None] = mapped_column(String(20))
    sender_value: Mapped[str | None] = mapped_column(String(100))
    doc_type_id: Mapped[str] = mapped_column(Text, nullable=False)
    process_id: Mapped[str] = mapped_column(Text, nullable=False)
    # Our idempotency key (= invoice.correlation_id.hex on outbound).
    business_message_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # AP/AS4-assigned MessageId — unique (partial, non-NULL) so a future inbound
    # slice can dedupe redeliveries. NULL until the adapter returns.
    message_id: Mapped[str | None] = mapped_column(String(255))
    # "sending" | "sent" | "delivered" | "failed". Live = {sending,sent,delivered}.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="sending")
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    # PII-free reason code only.
    failure_reason: Mapped[str | None] = mapped_column(String(255))
    transmitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Persisted transmission-summary amount — Decimal, never float.
    amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    # Adapter raw response (no PII; gateway echoes message id / status).
    raw_response: Mapped[dict | None] = mapped_column(JSONB)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # entity_id comes from EntityMixin; created_at/updated_at from TimestampMixin.

    __table_args__ = (
        # Constrain the enumerated columns at the DB level so a typo (e.g.
        # 'failure') can't slip past the partial-index predicate (WHERE
        # status <> 'failed') and strand a live row that never matches.
        CheckConstraint(
            "direction IN ('outbound','inbound')", name="ck_peppol_direction"
        ),
        CheckConstraint(
            "status IN ('sending','sent','delivered','failed')", name="ck_peppol_status"
        ),
        # The single-column ix_peppol_transmissions_{invoice_id,organization_id,
        # entity_id} indexes are auto-created by `index=True` on those columns
        # (and on EntityMixin.entity_id) with the same `ix_<table>_<col>` names
        # the 0034 migration uses — declaring them here too would make
        # create_all emit each twice (DuplicateTableError on a fresh tenant).
        # THE IDEMPOTENCY GUARD — at most one non-failed transmission per
        # (invoice_id, direction). A failed prior send is excluded, so a retry
        # is allowed. Predicate text MUST match the 0033 migration verbatim so a
        # fresh tenant built via create_all matches a migrated one.
        Index(
            "uq_peppol_one_live_per_invoice_direction",
            "invoice_id",
            "direction",
            unique=True,
            postgresql_where=text("status <> 'failed'"),
        ),
        # message_id dedupe — partial so many NULLs coexist.
        Index(
            "uq_peppol_message_id",
            "message_id",
            unique=True,
            postgresql_where=text("message_id IS NOT NULL"),
        ),
    )
