import enum
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, EntityMixin, TimestampMixin


class PurchaseOrder(Base, EntityMixin, TimestampMixin):
    __tablename__ = "purchase_orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_number: Mapped[str] = mapped_column(String(100), nullable=False)
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id")
    )
    total: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="open")

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    line_items: Mapped[list["POLineItem"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )


class POLineItem(Base, TimestampMixin):
    __tablename__ = "po_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))

    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="line_items")


class GoodsReceipt(Base, EntityMixin, TimestampMixin):
    __tablename__ = "goods_receipts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gr_number: Mapped[str] = mapped_column(String(100), nullable=False)
    po_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id")
    )
    received_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(30), default="received")

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    line_items: Mapped[list["GRLineItem"]] = relationship(
        back_populates="goods_receipt", cascade="all, delete-orphan"
    )


class GRLineItem(Base, TimestampMixin):
    __tablename__ = "gr_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    gr_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("goods_receipts.id"), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    quantity_received: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))

    goods_receipt: Mapped[GoodsReceipt] = relationship(back_populates="line_items")


# ===========================================================================
# Procurement / Requisitions (roadmap "Procurement / Requisitions")
# ---------------------------------------------------------------------------
# Six tenant-scoped tables added in migration 0041_procurement. All carry an
# inline ``organization_id`` (TenantMixin shape) + ``EntityMixin`` (subsidiary
# scope) + ``TimestampMixin``. Money is always ``Numeric(15, 2)`` (never float).
# Status fields are StrEnum mapped to ``Enum(..., native_enum=False)`` String
# columns (matches contract.py / expense.py). No circular FKs — create order is
# catalogs → catalog_items → budgets → purchase_requisitions →
# requisition_line_items → intake_requests (purchase_orders / contracts /
# vendors / gl_accounts already exist).
#
# Feature ownership (parallel build): requisitions + req→PO conversion,
# catalogs + guided buying, budgets, and intake forms each own their own API /
# service / schema / frontend files on top of these shared models.
# See ``backend/docs/procurement.md``.
# ===========================================================================


class RequisitionStatus(enum.StrEnum):
    draft = "draft"
    submitted = "submitted"
    pending_approval = "pending_approval"
    approved = "approved"
    rejected = "rejected"
    converted = "converted"  # turned into a purchase order
    cancelled = "cancelled"


class CatalogType(enum.StrEnum):
    internal = "internal"  # items stored in catalog_items
    punchout = "punchout"  # external supplier site (cXML/OCI punch-out)


class BudgetDimension(enum.StrEnum):
    department = "department"
    project = "project"
    cost_center = "cost_center"
    gl_account = "gl_account"


class IntakeType(enum.StrEnum):
    software = "software"
    services = "services"
    hardware = "hardware"
    other = "other"


class IntakeStatus(enum.StrEnum):
    open = "open"
    in_review = "in_review"
    approved = "approved"
    rejected = "rejected"
    converted = "converted"  # turned into a requisition or PO
    cancelled = "cancelled"


class Catalog(Base, EntityMixin, TimestampMixin):
    """A supplier or internal catalog. ``punchout`` catalogs point at an external
    supplier site; ``internal`` catalogs hold ``CatalogItem`` rows. ``is_preferred``
    drives guided buying (steer buyers to preferred vendors/contracts)."""

    __tablename__ = "catalogs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    catalog_type: Mapped[CatalogType] = mapped_column(
        Enum(CatalogType, native_enum=False, length=20),
        default=CatalogType.internal,
        nullable=False,
    )
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), index=True
    )
    punchout_url: Mapped[str | None] = mapped_column(String(500))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_preferred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    items: Mapped[list["CatalogItem"]] = relationship(
        back_populates="catalog", cascade="all, delete-orphan"
    )


class CatalogItem(Base, EntityMixin, TimestampMixin):
    """A purchasable line in an internal catalog (sku, price, uom, default GL)."""

    __tablename__ = "catalog_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalogs.id"), nullable=False, index=True
    )
    sku: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    # --- Money (Numeric, never float) -------------------------------------
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    uom: Mapped[str | None] = mapped_column(String(20))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), index=True
    )
    gl_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_accounts.id"), index=True
    )
    category: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    catalog: Mapped[Catalog] = relationship(back_populates="items")


class Budget(Base, EntityMixin, TimestampMixin):
    """A spend allocation for a department / project / cost-center / GL account
    over a period. ``amount`` is the allocation; spend is computed on read from
    requisitions / POs / invoices (no stored running total)."""

    __tablename__ = "budgets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dimension: Mapped[BudgetDimension] = mapped_column(
        Enum(BudgetDimension, native_enum=False, length=20),
        default=BudgetDimension.department,
        nullable=False,
    )
    # The dimension value this budget tracks, e.g. "Engineering" / "Project X".
    dimension_value: Mapped[str] = mapped_column(String(150), nullable=False)
    # Free-form period label, e.g. "2026" or "2026-Q2" (period_start/end bound it).
    period: Mapped[str | None] = mapped_column(String(20))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)

    # --- Money (Numeric, never float) -------------------------------------
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    notes: Mapped[str | None] = mapped_column(Text)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )


class PurchaseRequisition(Base, EntityMixin, TimestampMixin):
    """A purchase request raised by a buyer, routed for approval, then converted
    to a PurchaseOrder. ``converted_po_id`` links the resulting PO once approved
    and converted."""

    __tablename__ = "purchase_requisitions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requisition_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    requester_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    department: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[RequisitionStatus] = mapped_column(
        Enum(RequisitionStatus, native_enum=False, length=30),
        default=RequisitionStatus.draft,
        nullable=False,
    )
    needed_by: Mapped[date | None] = mapped_column(Date)
    justification: Mapped[str | None] = mapped_column(Text)

    # Optional links — preferred vendor / contract / budget (guided buying +
    # budget tracking). Plain FKs; no ORM relationship to keep features decoupled.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), index=True
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id"), index=True
    )
    budget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("budgets.id"), index=True
    )

    # --- Money (Numeric, never float) -------------------------------------
    total: Mapped[Decimal] = mapped_column(Numeric(15, 2), default=Decimal("0"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    notes: Mapped[str | None] = mapped_column(Text)

    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    converted_po_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id"), index=True
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    line_items: Mapped[list["RequisitionLineItem"]] = relationship(
        back_populates="requisition", cascade="all, delete-orphan"
    )


class RequisitionLineItem(Base, TimestampMixin):
    __tablename__ = "requisition_line_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requisition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requisitions.id"), nullable=False, index=True
    )
    line_number: Mapped[int | None] = mapped_column(Integer)
    # Optional source catalog item (internal catalog buying).
    catalog_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("catalog_items.id"), index=True
    )
    item_code: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    total: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    gl_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gl_accounts.id"), index=True
    )
    uom: Mapped[str | None] = mapped_column(String(20))

    requisition: Mapped[PurchaseRequisition] = relationship(back_populates="line_items")


class IntakeRequest(Base, EntityMixin, TimestampMixin):
    """An intake form for non-PO spend (software, services, etc.). Captures the
    ask before a vendor/PO exists; can be converted into a requisition or PO."""

    __tablename__ = "intake_requests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    request_number: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    request_type: Mapped[IntakeType] = mapped_column(
        Enum(IntakeType, native_enum=False, length=20),
        default=IntakeType.other,
        nullable=False,
    )
    requester_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    description: Mapped[str | None] = mapped_column(Text)

    # --- Money (Numeric, never float) -------------------------------------
    estimated_amount: Mapped[Decimal | None] = mapped_column(Numeric(15, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")

    # The vendor may not exist as a row yet — capture the name, link if known.
    vendor_name: Mapped[str | None] = mapped_column(String(255))
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendors.id"), index=True
    )
    status: Mapped[IntakeStatus] = mapped_column(
        Enum(IntakeStatus, native_enum=False, length=20),
        default=IntakeStatus.open,
        nullable=False,
    )
    # Flexible intake questionnaire answers (advisory; no PII).
    form_data: Mapped[dict | None] = mapped_column(JSONB)
    needed_by: Mapped[date | None] = mapped_column(Date)
    justification: Mapped[str | None] = mapped_column(Text)

    converted_requisition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_requisitions.id"), index=True
    )
    converted_po_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id"), index=True
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
