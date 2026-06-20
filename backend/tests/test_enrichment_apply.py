"""Coverage for applying an external-enrichment suggestion onto the Vendor.

``POST /api/enrichment/vendors/{id}/apply`` writes a steward-selected set of
enrichment fields (``name`` / ``address`` / ``website``) onto the Vendor through
an AUDITED, idempotent, RBAC-gated, tenant-scoped path. ``tax_id`` is NEVER
applyable here (it must go through the bank/tax change-request gate).

Real-Postgres end-to-end (``realdb``) — exercises the SQL, the audit diff row,
the non-destructive (only-named-fields) write, the tax_id exclusion, RBAC, the
idempotent re-apply, and tenant isolation.
"""

from __future__ import annotations

import uuid

from app.models.vendor import Vendor
from app.models.workflow import AuditLog


async def _seed_vendor(mk, org_id, *, name="Acme Supplies", address=None, website=None):
    async with mk() as s:
        vendor = Vendor(
            organization_id=org_id,
            name=name,
            status="active",
            address=address,
            website=website,
        )
        s.add(vendor)
        await s.commit()
        await s.refresh(vendor)
        return vendor.id


async def _audit_rows(mk, vendor_id):
    from sqlalchemy import select

    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "vendor",
                        AuditLog.entity_id == vendor_id,
                        AuditLog.action == "vendor.updated",
                    )
                    .order_by(AuditLog.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        return [dict(r.details or {}) for r in rows]


async def test_apply_writes_accepted_fields_and_audits(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id, name="Acme Supplies")

    body = {
        "fields": [
            {"field": "address", "value": "1 Mock Plaza, Suite 100"},
            {"field": "website", "value": "https://acmesupplies.example"},
        ]
    }
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{vid}/apply", json=body)
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data["applied"].keys()) == {"address", "website"}
    assert data["applied"]["website"]["new"] == "https://acmesupplies.example"
    assert data["vendor"]["website"] == "https://acmesupplies.example"
    assert data["vendor"]["address"] == "1 Mock Plaza, Suite 100"

    # The values actually landed on the row.
    async with mk() as s:
        v = await s.get(Vendor, vid)
        assert v.address == "1 Mock Plaza, Suite 100"
        assert v.website == "https://acmesupplies.example"

    # Exactly one vendor.updated audit row carrying the before/after diff.
    details = await _audit_rows(mk, vid)
    assert len(details) == 1
    changes = details[0]["changes"]
    assert changes["address"]["old"] is None
    assert changes["address"]["new"] == "1 Mock Plaza, Suite 100"
    assert details[0]["source"] == "enrichment_apply"


async def test_apply_only_changes_accepted_fields(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id, name="Acme Supplies", address="Old Address")

    # Only website accepted — address must stay untouched (non-destructive).
    body = {"fields": [{"field": "website", "value": "https://new.example"}]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{vid}/apply", json=body)
    assert r.status_code == 200, r.text
    assert set(r.json()["applied"].keys()) == {"website"}

    async with mk() as s:
        v = await s.get(Vendor, vid)
        assert v.address == "Old Address"  # untouched
        assert v.website == "https://new.example"


async def test_apply_rejects_tax_id(realdb):
    """tax_id is a fraud surface — never applyable from enrichment (422)."""
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id)

    body = {"fields": [{"field": "tax_id", "value": "99-9999999"}]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{vid}/apply", json=body)
    assert r.status_code == 422, r.text
    assert "tax_id" in r.json()["detail"]

    # Nothing written.
    async with mk() as s:
        v = await s.get(Vendor, vid)
        assert v.tax_id is None
    assert await _audit_rows(mk, vid) == []


async def test_apply_rejects_unknown_field(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id)
    body = {"fields": [{"field": "bank_details", "value": "x"}]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{vid}/apply", json=body)
    assert r.status_code == 422


async def test_apply_blank_name_rejected(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id, name="Acme Supplies")
    body = {"fields": [{"field": "name", "value": "  "}]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{vid}/apply", json=body)
    assert r.status_code == 422
    async with mk() as s:
        assert (await s.get(Vendor, vid)).name == "Acme Supplies"


async def test_apply_idempotent_reapply_no_spurious_audit(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id, website="https://acme.example")

    body = {"fields": [{"field": "website", "value": "https://acme.example"}]}
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(f"/api/enrichment/vendors/{vid}/apply", json=body)
    assert r.status_code == 200, r.text
    # Same value already on the row → no diff, no audit row.
    assert r.json()["applied"] == {}
    assert await _audit_rows(mk, vid) == []


async def test_apply_clerk_forbidden(realdb):
    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vid = await _seed_vendor(mk, org_id)
    body = {"fields": [{"field": "website", "value": "https://x.example"}]}
    async with realdb.client(key="a", role="ap_clerk") as clerk:
        r = await clerk.post(f"/api/enrichment/vendors/{vid}/apply", json=body)
    assert r.status_code == 403


async def test_apply_auth_required(realdb):
    async with realdb.client(key="a", role=None) as client:
        r = await client.post(
            f"/api/enrichment/vendors/{uuid.uuid4()}/apply",
            json={"fields": [{"field": "website", "value": "https://x.example"}]},
        )
    assert r.status_code == 401


async def test_apply_unknown_vendor_404(realdb):
    async with realdb.client(key="a", role="ap_manager") as client:
        r = await client.post(
            f"/api/enrichment/vendors/{uuid.uuid4()}/apply",
            json={"fields": [{"field": "website", "value": "https://x.example"}]},
        )
    assert r.status_code == 404


async def test_apply_tenant_isolation(realdb):
    mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    vid = await _seed_vendor(mk_a, org_a)
    body = {"fields": [{"field": "website", "value": "https://x.example"}]}
    async with realdb.client(key="b", role="ap_manager") as client_b:
        r = await client_b.post(f"/api/enrichment/vendors/{vid}/apply", json=body)
    assert r.status_code == 404
