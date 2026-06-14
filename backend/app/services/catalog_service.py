"""Guided-buying suggestion logic for the procurement catalogs vertical.

Keeps the catalogs router thin: given buyer criteria (category / vendor /
free-text) this module ranks the *preferred sources* a buyer should be steered
toward before raising a requisition — preferred vendors (vendors that own an
active, preferred catalog), vendors with an active contract on file, and the
matching active catalog items. Pure read logic, deterministic, no LLM, no
external calls.

Ranking (highest intent first):
  1. Preferred vendors — own an active catalog flagged ``is_preferred``. These
     are the org's curated, negotiated sources.
  2. In-contract vendors — have an ``active`` :class:`Contract`. Buying against
     an existing contract keeps spend on-agreement (and feeds spend-to-contract
     tracking).
  3. Matching catalog items — active items in active catalogs, preferred items
     ranked ahead of the rest, so the buyer sees a concrete line + price.

Everything is entity-scoped by the caller (it passes the already-scoped base
selects in via the model + entity_id), so a suggestion never leaks rows from a
sibling subsidiary.
"""

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract, ContractStatus
from app.models.procurement import Catalog, CatalogItem
from app.models.vendor import Vendor
from app.schemas.catalog import (
    GuidedBuyingItem,
    GuidedBuyingSuggestion,
    GuidedBuyingVendor,
)
from app.tenant import apply_entity_scope

# Cap each list so the suggestion stays a focused steer, not a full export.
_MAX_VENDORS = 25
_MAX_ITEMS = 50


async def build_guided_buying_suggestion(
    db: AsyncSession,
    *,
    entity_id: uuid.UUID | None,
    category: str | None = None,
    vendor_id: uuid.UUID | None = None,
    q: str | None = None,
) -> GuidedBuyingSuggestion:
    """Assemble the guided-buying steer for the given criteria.

    ``entity_id`` scopes every read to one subsidiary (or all, when ``None``).
    Filters are AND-combined and all optional — with no filters the result is
    the org's preferred / in-contract vendors plus a sample of catalog items.
    """
    preferred_vendors = await _preferred_vendors(db, entity_id, vendor_id, category)
    in_contract_vendors = await _in_contract_vendors(db, entity_id, vendor_id)
    items = await _matching_items(db, entity_id, category, vendor_id, q)
    return GuidedBuyingSuggestion(
        preferred_vendors=preferred_vendors,
        in_contract_vendors=in_contract_vendors,
        items=items,
    )


async def _preferred_vendors(
    db: AsyncSession,
    entity_id: uuid.UUID | None,
    vendor_id: uuid.UUID | None,
    category: str | None,
) -> list[GuidedBuyingVendor]:
    """Vendors that own an active, preferred catalog.

    Joins each preferred catalog to its vendor; an active contract (if any) is
    attached so the requisition can link it. When ``category`` is given, the
    catalog must carry at least one active item in that category to qualify (the
    preference is meaningful for what the buyer is actually after)."""
    base = apply_entity_scope(
        select(Catalog, Vendor.name)
        .join(Vendor, Vendor.id == Catalog.vendor_id)
        .where(
            Catalog.is_preferred.is_(True),
            Catalog.is_active.is_(True),
            Catalog.vendor_id.isnot(None),
        ),
        Catalog,
        entity_id,
    )
    if vendor_id is not None:
        base = base.where(Catalog.vendor_id == vendor_id)
    if category:
        base = base.where(
            Catalog.id.in_(
                select(CatalogItem.catalog_id).where(
                    CatalogItem.category == category,
                    CatalogItem.is_active.is_(True),
                )
            )
        )
    base = base.order_by(Vendor.name).limit(_MAX_VENDORS)
    rows = (await db.execute(base)).all()

    out: list[GuidedBuyingVendor] = []
    for catalog, vendor_name in rows:
        contract = await _active_contract_for_vendor(db, entity_id, catalog.vendor_id)
        reasons = ["preferred_catalog"]
        if contract is not None:
            reasons.append("active_contract")
        out.append(
            GuidedBuyingVendor(
                vendor_id=str(catalog.vendor_id),
                vendor_name=vendor_name,
                reasons=reasons,
                contract_id=str(contract.id) if contract else None,
                contract_number=contract.contract_number if contract else None,
                catalog_id=str(catalog.id),
                catalog_name=catalog.name,
            )
        )
    return out


async def _in_contract_vendors(
    db: AsyncSession,
    entity_id: uuid.UUID | None,
    vendor_id: uuid.UUID | None,
) -> list[GuidedBuyingVendor]:
    """Vendors with an active contract on file (one row per vendor — the most
    recently-started contract represents them)."""
    base = apply_entity_scope(
        select(Contract, Vendor.name)
        .join(Vendor, Vendor.id == Contract.vendor_id)
        .where(Contract.status == ContractStatus.active),
        Contract,
        entity_id,
    )
    if vendor_id is not None:
        base = base.where(Contract.vendor_id == vendor_id)
    base = base.order_by(Contract.start_date.desc().nullslast())
    rows = (await db.execute(base)).all()

    seen: set[uuid.UUID] = set()
    out: list[GuidedBuyingVendor] = []
    for contract, vendor_name in rows:
        if contract.vendor_id in seen:
            continue
        seen.add(contract.vendor_id)
        out.append(
            GuidedBuyingVendor(
                vendor_id=str(contract.vendor_id),
                vendor_name=vendor_name,
                reasons=["active_contract"],
                contract_id=str(contract.id),
                contract_number=contract.contract_number,
                catalog_id=None,
                catalog_name=None,
            )
        )
        if len(out) >= _MAX_VENDORS:
            break
    return out


async def _matching_items(
    db: AsyncSession,
    entity_id: uuid.UUID | None,
    category: str | None,
    vendor_id: uuid.UUID | None,
    q: str | None,
) -> list[GuidedBuyingItem]:
    """Active catalog items (in active catalogs) matching the criteria, with
    items from preferred catalogs ranked first."""
    base = apply_entity_scope(
        select(CatalogItem, Catalog.name, Catalog.is_preferred)
        .join(Catalog, Catalog.id == CatalogItem.catalog_id)
        .where(
            CatalogItem.is_active.is_(True),
            Catalog.is_active.is_(True),
        ),
        CatalogItem,
        entity_id,
    )
    if category:
        base = base.where(CatalogItem.category == category)
    if vendor_id is not None:
        base = base.where(or_(CatalogItem.vendor_id == vendor_id, Catalog.vendor_id == vendor_id))
    if q:
        like = f"%{q.strip()}%"
        base = base.where(
            or_(
                CatalogItem.name.ilike(like),
                CatalogItem.sku.ilike(like),
                CatalogItem.description.ilike(like),
            )
        )
    # Preferred catalogs first, then by item name for a stable order.
    base = base.order_by(Catalog.is_preferred.desc(), CatalogItem.name).limit(_MAX_ITEMS)
    rows = (await db.execute(base)).all()

    return [
        GuidedBuyingItem(
            catalog_item_id=str(item.id),
            catalog_id=str(item.catalog_id),
            catalog_name=catalog_name,
            sku=item.sku,
            name=item.name,
            unit_price=float(item.unit_price) if item.unit_price is not None else None,
            currency=item.currency,
            uom=item.uom,
            vendor_id=str(item.vendor_id) if item.vendor_id else None,
            category=item.category,
            is_preferred=bool(is_preferred),
        )
        for item, catalog_name, is_preferred in rows
    ]


async def _active_contract_for_vendor(
    db: AsyncSession,
    entity_id: uuid.UUID | None,
    vendor_id: uuid.UUID | None,
) -> Contract | None:
    if vendor_id is None:
        return None
    query = apply_entity_scope(
        select(Contract).where(
            Contract.vendor_id == vendor_id,
            Contract.status == ContractStatus.active,
        ),
        Contract,
        entity_id,
    ).order_by(Contract.start_date.desc().nullslast())
    return (await db.execute(query)).scalars().first()
