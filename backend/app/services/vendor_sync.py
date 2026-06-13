"""Vendor ERP sync service — pull vendors from ERP and sync to local database."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vendor import Vendor


async def sync_vendors_from_erp(
    db: AsyncSession,
    organization_id: uuid.UUID,
    erp_vendors: list[dict],
    entity_id: uuid.UUID | None = None,
) -> dict:
    """Sync a list of vendor records from an ERP into the local database.

    ``entity_id`` (multi-entity Phase 2) is the entity newly-created vendors
    land under — the selected entity or the tenant default, resolved at the
    endpoint. Vendors matched/updated keep their existing entity.

    Each erp_vendor dict should have:
        - erp_vendor_id: str (required — the vendor ID in the ERP)
        - name: str
        - code: str | None
        - email: str | None
        - phone: str | None
        - address: str | None
        - tax_id: str | None
        - payment_terms: str | None

    Returns summary: {created: int, updated: int, unchanged: int}
    """
    now = datetime.now(UTC)
    created = 0
    updated = 0
    unchanged = 0

    for erp_v in erp_vendors:
        erp_id = erp_v.get("erp_vendor_id")
        if not erp_id:
            continue

        # Check if vendor already exists by ERP ID
        result = await db.execute(
            select(Vendor).where(
                Vendor.erp_vendor_id == erp_id,
                Vendor.organization_id == organization_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            # Update fields if changed
            changed = False
            for field in ("name", "code", "email", "phone", "address", "tax_id", "payment_terms"):
                new_val = erp_v.get(field)
                if new_val is not None and getattr(existing, field) != new_val:
                    setattr(existing, field, new_val)
                    changed = True

            existing.erp_synced_at = now
            if changed:
                updated += 1
            else:
                unchanged += 1
        else:
            # Also check if there's a name match (might be manually created before ERP sync)
            name = erp_v.get("name", "")
            if name:
                result = await db.execute(
                    select(Vendor).where(
                        Vendor.name == name,
                        Vendor.organization_id == organization_id,
                        Vendor.erp_vendor_id.is_(None),
                    )
                )
                name_match = result.scalar_one_or_none()
                if name_match:
                    # Link existing vendor to ERP
                    name_match.erp_vendor_id = erp_id
                    name_match.erp_synced_at = now
                    for field in ("code", "email", "phone", "address", "tax_id", "payment_terms"):
                        new_val = erp_v.get(field)
                        if new_val is not None:
                            setattr(name_match, field, new_val)
                    if name_match.status == "unverified":
                        name_match.status = "active"
                        name_match.source = "erp_sync"
                    updated += 1
                    continue

            # Create new vendor
            vendor = Vendor(
                name=name or f"ERP Vendor {erp_id}",
                code=erp_v.get("code"),
                email=erp_v.get("email"),
                phone=erp_v.get("phone"),
                address=erp_v.get("address"),
                tax_id=erp_v.get("tax_id"),
                payment_terms=erp_v.get("payment_terms"),
                erp_vendor_id=erp_id,
                erp_synced_at=now,
                status="active",
                source="erp_sync",
                organization_id=organization_id,
                entity_id=entity_id,
            )
            db.add(vendor)
            created += 1

    return {"created": created, "updated": updated, "unchanged": unchanged}
