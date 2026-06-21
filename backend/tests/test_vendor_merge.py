"""Coverage for the vendor-consolidation EXECUTE path.

``POST /api/enrichment/vendors/consolidation/merge`` folds a set of duplicate
vendors into one canonical vendor: it reassigns every ``vendor_id`` FK across
the tenant child tables (so nothing orphans), soft-retires the duplicates
(``status="inactive"`` — never hard-deleted), is idempotent on a re-run, writes
a PII-free ``vendor.merged`` audit row, and refuses self-merge / cross-entity /
unknown vendors. RBAC: ``vendor.manage`` (admin / ap_manager).

Real-Postgres end-to-end (``realdb``) — exercises the SQL UPDATEs, the audit
row, the idempotency, the refusals, RBAC, and tenant isolation.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models.contract import Contract, ContractStatus, ContractType
from app.models.credit_memo import CreditMemo
from app.models.invoice import Invoice, InvoiceStatus
from app.models.procurement import PurchaseOrder
from app.models.vendor import Vendor
from app.models.workflow import AuditLog


async def _seed_vendor(mk, org_id, *, name="Acme", status="active", entity_id=None):
    async with mk() as s:
        v = Vendor(organization_id=org_id, name=name, status=status, entity_id=entity_id)
        s.add(v)
        await s.commit()
        await s.refresh(v)
        return v.id


async def _default_entity_id(mk):
    from app.models.entity import Entity

    async with mk() as s:
        return (await s.execute(select(Entity.id).where(Entity.is_default.is_(True)))).scalar_one()


async def _seed_invoice(mk, org_id, vendor_id, *, number, entity_id):
    from decimal import Decimal

    async with mk() as s:
        inv = Invoice(
            organization_id=org_id,
            entity_id=entity_id,
            invoice_number=number,
            vendor_name="x",
            amount=Decimal("100.00"),
            vendor_id=vendor_id,
            status=InvoiceStatus.new,
        )
        s.add(inv)
        await s.commit()
        await s.refresh(inv)
        return inv.id


async def _seed_po(mk, org_id, vendor_id, *, number, entity_id):
    from decimal import Decimal

    async with mk() as s:
        po = PurchaseOrder(
            organization_id=org_id,
            entity_id=entity_id,
            po_number=number,
            vendor_id=vendor_id,
            total=Decimal("500.00"),
            status="open",
        )
        s.add(po)
        await s.commit()
        await s.refresh(po)
        return po.id


async def _seed_credit_memo(mk, org_id, vendor_id, *, entity_id):
    from decimal import Decimal

    async with mk() as s:
        cm = CreditMemo(
            organization_id=org_id,
            entity_id=entity_id,
            vendor_id=vendor_id,
            memo_number=f"CM-{uuid.uuid4().hex[:8]}",
            amount=Decimal("10.00"),
            status="open",
        )
        s.add(cm)
        await s.commit()
        await s.refresh(cm)
        return cm.id


async def _audit_merge_rows(mk, canonical_id):
    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "vendor",
                        AuditLog.entity_id == canonical_id,
                        AuditLog.action == "vendor.merged",
                    )
                    .order_by(AuditLog.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [dict(r.details or {}) for r in rows]


async def test_merge_reassigns_fks_and_deactivates_duplicates(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    ent = await _default_entity_id(mk)

    canonical = await _seed_vendor(mk, org_id, name="Acme Inc")
    dup1 = await _seed_vendor(mk, org_id, name="Acme Incorporated")
    dup2 = await _seed_vendor(mk, org_id, name="ACME, LLC")

    # Children spread across the duplicates.
    inv1 = await _seed_invoice(mk, org_id, dup1, number="INV-1", entity_id=ent)
    inv2 = await _seed_invoice(mk, org_id, dup2, number="INV-2", entity_id=ent)
    po1 = await _seed_po(mk, org_id, dup1, number="PO-1", entity_id=ent)
    cm1 = await _seed_credit_memo(mk, org_id, dup2, entity_id=ent)

    body = {"canonical_vendor_id": str(canonical), "duplicate_vendor_ids": [str(dup1), str(dup2)]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post("/api/enrichment/vendors/consolidation/merge", json=body)
    assert r.status_code == 200, r.text
    data = r.json()

    # Every child FK moved to the canonical vendor.
    assert data["reassigned"]["invoices"] == 2
    assert data["reassigned"]["purchase_orders"] == 1
    assert data["reassigned"]["credit_memos"] == 1
    assert data["total_reassigned"] == 4
    assert set(data["deactivated_vendor_ids"]) == {str(dup1), str(dup2)}

    async with mk() as s:
        assert (await s.get(Invoice, inv1)).vendor_id == canonical
        assert (await s.get(Invoice, inv2)).vendor_id == canonical
        assert (await s.get(PurchaseOrder, po1)).vendor_id == canonical
        assert (await s.get(CreditMemo, cm1)).vendor_id == canonical
        # Duplicates soft-retired, canonical untouched & active. Not deleted.
        assert (await s.get(Vendor, dup1)).status == "inactive"
        assert (await s.get(Vendor, dup2)).status == "inactive"
        assert (await s.get(Vendor, canonical)).status == "active"

    # Exactly one PII-free vendor.merged audit row.
    details = await _audit_merge_rows(mk, canonical)
    assert len(details) == 1
    d = details[0]
    assert d["canonical_vendor_id"] == str(canonical)
    assert set(d["duplicate_vendor_ids"]) == {str(dup1), str(dup2)}
    assert d["total_reassigned"] == 4
    assert d["reassigned"]["invoices"] == 2


async def test_merge_is_idempotent(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    ent = await _default_entity_id(mk)

    canonical = await _seed_vendor(mk, org_id, name="Globex")
    dup = await _seed_vendor(mk, org_id, name="Globex Corp")
    await _seed_invoice(mk, org_id, dup, number="INV-G", entity_id=ent)

    body = {"canonical_vendor_id": str(canonical), "duplicate_vendor_ids": [str(dup)]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r1 = await client.post("/api/enrichment/vendors/consolidation/merge", json=body)
        assert r1.status_code == 200, r1.text
        assert r1.json()["total_reassigned"] == 1
        assert r1.json()["deactivated_vendor_ids"] == [str(dup)]

        # Re-run: FKs already moved, duplicate already inactive → no-op (not error).
        r2 = await client.post("/api/enrichment/vendors/consolidation/merge", json=body)
        assert r2.status_code == 200, r2.text
        assert r2.json()["total_reassigned"] == 0
        assert r2.json()["deactivated_vendor_ids"] == []

    # The re-run still wrote its own audit row (append-only) but moved nothing.
    details = await _audit_merge_rows(mk, canonical)
    assert len(details) == 2
    assert details[1]["total_reassigned"] == 0


async def test_merge_refuses_self_merge(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    v = await _seed_vendor(mk, org_id, name="SelfCo")
    body = {"canonical_vendor_id": str(v), "duplicate_vendor_ids": [str(v)]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post("/api/enrichment/vendors/consolidation/merge", json=body)
    assert r.status_code == 422
    assert "self-merge" in r.json()["detail"].lower()


async def test_merge_refuses_empty_duplicates(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    v = await _seed_vendor(mk, org_id, name="EmptyCo")
    body = {"canonical_vendor_id": str(v), "duplicate_vendor_ids": []}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post("/api/enrichment/vendors/consolidation/merge", json=body)
    assert r.status_code == 422


async def test_merge_refuses_cross_entity(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id

    # A second, non-default entity.
    from app.models.entity import Entity

    async with mk() as s:
        other = Entity(
            organization_id=org_id, name="Sub B", slug="sub-b", currency="USD", is_default=False
        )
        s.add(other)
        await s.commit()
        await s.refresh(other)
        other_ent = other.id
    default_ent = await _default_entity_id(mk)

    canonical = await _seed_vendor(mk, org_id, name="CrossCo", entity_id=default_ent)
    dup = await _seed_vendor(mk, org_id, name="CrossCo B", entity_id=other_ent)

    body = {"canonical_vendor_id": str(canonical), "duplicate_vendor_ids": [str(dup)]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post("/api/enrichment/vendors/consolidation/merge", json=body)
    assert r.status_code == 422
    assert "entit" in r.json()["detail"].lower()

    # Nothing changed — the duplicate stays active.
    async with mk() as s:
        assert (await s.get(Vendor, dup)).status == "active"


async def test_merge_unknown_vendor_404(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    canonical = await _seed_vendor(mk, org_id, name="KnownCo")
    body = {"canonical_vendor_id": str(canonical), "duplicate_vendor_ids": [str(uuid.uuid4())]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post("/api/enrichment/vendors/consolidation/merge", json=body)
    assert r.status_code == 404


async def test_merge_clerk_forbidden(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    canonical = await _seed_vendor(mk, org_id, name="ClerkCo")
    dup = await _seed_vendor(mk, org_id, name="ClerkCo Dup")
    body = {"canonical_vendor_id": str(canonical), "duplicate_vendor_ids": [str(dup)]}
    async with realdb.client(key="a", role="ap_clerk") as clerk:
        r = await clerk.post("/api/enrichment/vendors/consolidation/merge", json=body)
    assert r.status_code == 403
    # Nothing retired.
    async with mk() as s:
        assert (await s.get(Vendor, dup)).status == "active"


async def test_merge_auth_required(realdb):
    async with realdb.client(key="a", role=None) as client:
        r = await client.post(
            "/api/enrichment/vendors/consolidation/merge",
            json={
                "canonical_vendor_id": str(uuid.uuid4()),
                "duplicate_vendor_ids": [str(uuid.uuid4())],
            },
        )
    assert r.status_code == 401


async def test_merge_tenant_isolation(realdb):
    """A vendor from tenant A is unknown to tenant B → 404, no cross-tenant reach."""
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    canonical_a = await _seed_vendor(mk_a, org_a, name="IsoCo")
    dup_a = await _seed_vendor(mk_a, org_a, name="IsoCo Dup")

    body = {"canonical_vendor_id": str(canonical_a), "duplicate_vendor_ids": [str(dup_a)]}
    async with realdb.client(key="b", role="ap_manager") as client_b:
        r = await client_b.post("/api/enrichment/vendors/consolidation/merge", json=body)
    assert r.status_code == 404
    # A's vendors untouched.
    async with mk_a() as s:
        assert (await s.get(Vendor, dup_a)).status == "active"


async def test_merge_contract_fk_reassigned(realdb):
    """A non-nullable vendor_id FK table (contracts) also reassigns cleanly."""
    from datetime import date

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    ent = await _default_entity_id(mk)
    canonical = await _seed_vendor(mk, org_id, name="ContractCo")
    dup = await _seed_vendor(mk, org_id, name="ContractCo Dup")

    async with mk() as s:
        c = Contract(
            organization_id=org_id,
            entity_id=ent,
            vendor_id=dup,
            contract_number=f"C-{uuid.uuid4().hex[:8]}",
            title="Service",
            contract_type=ContractType.service,
            status=ContractStatus.active,
            start_date=date(2026, 1, 1),
        )
        s.add(c)
        await s.commit()
        await s.refresh(c)
        cid = c.id

    body = {"canonical_vendor_id": str(canonical), "duplicate_vendor_ids": [str(dup)]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post("/api/enrichment/vendors/consolidation/merge", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["reassigned"]["contracts"] == 1
    async with mk() as s:
        assert (await s.get(Contract, cid)).vendor_id == canonical
