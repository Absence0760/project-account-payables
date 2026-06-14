"""Pydantic request/response schemas for the catalogs + guided-buying router.

Money convention (mirrors ``schemas/expense.py`` / ``schemas/contract.py``):
request fields are typed ``Decimal | None`` for exactness on the way in;
response/list fields serialise money as ``float | None`` (the router does
``float(...)``). Never ``float`` on a column or in-memory total.

Catalogs are configuration-like (supplier / internal catalogs + their items);
``is_preferred`` steers guided buying. ``GuidedBuyingSuggestion`` is the
read-only steering surface — it ranks preferred sources, surfaces in-contract
vendors, and returns matching active catalog items so a buyer is pointed at the
right vendor / contract / catalog line before raising a requisition.
"""

from decimal import Decimal

from pydantic import BaseModel, Field

from app.api.pagination import PageMeta
from app.models.procurement import CatalogType

# ---------------------------------------------------------------------------
# Catalog items
# ---------------------------------------------------------------------------


class CatalogItemBase(BaseModel):
    sku: str | None = Field(default=None, max_length=100)
    name: str = Field(..., max_length=255)
    description: str | None = None
    unit_price: Decimal | None = None
    currency: str = Field(default="USD", max_length=3)
    uom: str | None = Field(default=None, max_length=20)
    vendor_id: str | None = None
    gl_account_id: str | None = None
    category: str | None = Field(default=None, max_length=100)
    is_active: bool = True


class CatalogItemCreate(CatalogItemBase):
    pass


class CatalogItemUpdate(BaseModel):
    """PATCH — every field optional."""

    sku: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=255)
    description: str | None = None
    unit_price: Decimal | None = None
    currency: str | None = Field(default=None, max_length=3)
    uom: str | None = Field(default=None, max_length=20)
    vendor_id: str | None = None
    gl_account_id: str | None = None
    category: str | None = Field(default=None, max_length=100)
    is_active: bool | None = None


class CatalogItemResponse(BaseModel):
    id: str
    catalog_id: str
    sku: str | None
    name: str
    description: str | None
    unit_price: float | None
    currency: str
    uom: str | None
    vendor_id: str | None
    gl_account_id: str | None
    category: str | None
    is_active: bool
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------


class CatalogBase(BaseModel):
    name: str = Field(..., max_length=255)
    catalog_type: CatalogType = CatalogType.internal
    vendor_id: str | None = None
    # Punch-out site URL — stored config only; live cXML/OCI round-trips are a
    # future extension (see backend/docs/procurement-catalogs.md).
    punchout_url: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    is_preferred: bool = False
    description: str | None = None


class CatalogCreate(CatalogBase):
    pass


class CatalogUpdate(BaseModel):
    """PATCH — every field optional."""

    name: str | None = Field(default=None, max_length=255)
    catalog_type: CatalogType | None = None
    vendor_id: str | None = None
    punchout_url: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None
    is_preferred: bool | None = None
    description: str | None = None


class CatalogResponse(BaseModel):
    id: str
    name: str
    catalog_type: str
    vendor_id: str | None
    punchout_url: str | None
    is_active: bool
    is_preferred: bool
    description: str | None
    item_count: int = 0
    items: list[CatalogItemResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str


class CatalogListResponse(PageMeta):
    items: list[CatalogResponse]
    total: int


# ---------------------------------------------------------------------------
# Guided buying — read-only steering surface
# ---------------------------------------------------------------------------


class GuidedBuyingVendor(BaseModel):
    """A vendor surfaced as a preferred / in-contract source for a buyer."""

    vendor_id: str
    vendor_name: str
    # Why this vendor is recommended (ranked highest first):
    #   "preferred_catalog" — the vendor owns an active, preferred catalog
    #   "active_contract"   — the vendor has an active contract on file
    reasons: list[str] = Field(default_factory=list)
    # Linked active contract (if any) so the requisition can attach it.
    contract_id: str | None = None
    contract_number: str | None = None
    # The vendor's preferred catalog (if any) so the buyer can browse it.
    catalog_id: str | None = None
    catalog_name: str | None = None


class GuidedBuyingItem(BaseModel):
    """A matching active catalog item the buyer should prefer."""

    catalog_item_id: str
    catalog_id: str
    catalog_name: str
    sku: str | None
    name: str
    unit_price: float | None
    currency: str
    uom: str | None
    vendor_id: str | None
    category: str | None
    # True when the owning catalog is flagged preferred (ranked first).
    is_preferred: bool


class GuidedBuyingSuggestion(BaseModel):
    """Read-only guided-buying result. Steers a buyer to preferred vendors,
    in-contract vendors, and matching catalog lines for the given criteria
    (``category`` / ``vendor_id`` / free-text ``q``). Deterministic, no LLM."""

    preferred_vendors: list[GuidedBuyingVendor] = Field(default_factory=list)
    in_contract_vendors: list[GuidedBuyingVendor] = Field(default_factory=list)
    items: list[GuidedBuyingItem] = Field(default_factory=list)


__all__ = [
    "CatalogType",
    "CatalogItemBase",
    "CatalogItemCreate",
    "CatalogItemUpdate",
    "CatalogItemResponse",
    "CatalogBase",
    "CatalogCreate",
    "CatalogUpdate",
    "CatalogResponse",
    "CatalogListResponse",
    "GuidedBuyingVendor",
    "GuidedBuyingItem",
    "GuidedBuyingSuggestion",
]
