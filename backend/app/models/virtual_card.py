"""Virtual card and rebate models."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EntityMixin, TimestampMixin


class VirtualCard(Base, EntityMixin, TimestampMixin):
    __tablename__ = "virtual_cards"

    # At most one LIVE (non-cancelled) card per invoice — the DB-level
    # idempotency backstop so a retried issuance can't mint a second provider
    # card. Mirrors migration 0067; a cancelled card is excluded so a
    # cancel-then-reissue still works. (Declared here so fresh tenants built via
    # create_all in tenant_provisioning get it too, not only migrated ones.)
    __table_args__ = (
        Index(
            "uq_virtual_cards_one_live_per_invoice",
            "invoice_id",
            unique=True,
            postgresql_where=text("status <> 'cancelled'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("invoices.id"), nullable=False
    )
    payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("payments.id")
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id")
    )
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    card_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_card_id: Mapped[str] = mapped_column(String(255), nullable=False)
    last_four: Mapped[str | None] = mapped_column(String(4))
    amount_limit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    amount_charged: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    status: Mapped[str] = mapped_column(String(30), default="created")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    charged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    merchant_name: Mapped[str | None] = mapped_column(String(255))
    decline_reason: Mapped[str | None] = mapped_column(String(500))

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )


class CardRevealToken(Base, TimestampMixin):
    """Single-use vendor-facing token. Mints when a card is issued; the
    plaintext goes in the email link, only the sha256 hash is persisted.
    The portal `/api/portal/cards/{token}` endpoint resolves the hash,
    checks expires_at + used_at, and stamps used_at on first reveal."""

    __tablename__ = "card_reveal_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_cards.id"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CardRebate(Base, TimestampMixin):
    __tablename__ = "card_rebates"

    # One rebate per virtual card — a single-use card yields exactly one
    # settlement → exactly one rebate. This unique index is the hard DB-level
    # backstop against a double-rebate under a race / Redis-outage (the webhook
    # already guards on card.status == "charged" + event-id dedup). Mirrors
    # migration 0069; declared here so fresh tenants built via create_all in
    # tenant_provisioning get it too, not only migrated ones.
    __table_args__ = (Index("uq_card_rebates_virtual_card", "virtual_card_id", unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    virtual_card_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("virtual_cards.id"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    period: Mapped[str | None] = mapped_column(String(7))  # e.g. "2026-04"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
