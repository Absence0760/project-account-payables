"""GDPR / CCPA privacy endpoints — /api/privacy DSAR export + erasure.

Covers (against real Postgres via the ``realdb`` fixture):
  * DSAR bundle assembles the subject's PII + related rows (user / vendor_user /
    vendor_contact).
  * Erasure redacts every PII field, leaves Invoice/Payment money fields and the
    append-only audit_log untouched, and is idempotent.
  * Tenant isolation — a subject in tenant A is neither exported nor erased when
    acting as tenant B.
  * The request itself is audited + recorded PII-free in data_subject_requests.

RBAC (admin-only) is also enforced by test_rbac.py's coverage gate; here we add a
direct non-admin 403 check on the DSAR route.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.data_subject_request import DataSubjectRequest
from app.models.invoice import Invoice, InvoiceStatus
from app.models.payment import Payment
from app.models.vendor import Vendor
from app.models.vendor_user import VendorUser
from app.models.workflow import AuditLog
from app.utils.passwords import pwd_context

# ---------------------------------------------------------------------------
# Helpers — seed a vendor (+ portal user, invoice, payment) in a tenant.
# ---------------------------------------------------------------------------


async def _seed_vendor_with_records(tenant_mk, org_id, *, with_portal=True):
    """Create a vendor with contact PII, one invoice, one payment, and
    (optionally) a portal user. Returns (vendor_id, vendor_user_id, invoice_id)."""
    vendor_id = uuid.uuid4()
    invoice_id = uuid.uuid4()
    vendor_user_id = uuid.uuid4()
    async with tenant_mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                organization_id=org_id,
                name="Acme Supplies Ltd",
                code="V-001",
                email="contact@acmesupplies.test",
                phone="+1-555-0100",
                address="123 Market St, Springfield",
                tax_id="12-3456789",
                bank_details={"account": "000111222", "routing": "021000021"},
                beneficial_owner_data={"owner": "Jane Doe"},
                status="active",
            )
        )
        s.add(
            Invoice(
                id=invoice_id,
                correlation_id=uuid.uuid4(),
                organization_id=org_id,
                invoice_number="INV-9001",
                vendor_name="Acme Supplies Ltd",
                vendor_id=vendor_id,
                amount=Decimal("4200.50"),
                currency="USD",
                status=InvoiceStatus.approved,
            )
        )
        await s.flush()  # ensure the invoice exists before its FK-dependent rows
        s.add(
            Payment(
                id=uuid.uuid4(),
                invoice_id=invoice_id,
                amount=Decimal("4200.50"),
                method="ach",
                status="completed",
            )
        )
        if with_portal:
            s.add(
                VendorUser(
                    id=vendor_user_id,
                    vendor_id=vendor_id,
                    email="portal@acmesupplies.test",
                    full_name="Portal Person",
                    hashed_password=pwd_context.hash("Passw0rd!xyz"),
                    is_active=True,
                )
            )
        await s.commit()
    return vendor_id, vendor_user_id, invoice_id


# ---------------------------------------------------------------------------
# DSAR export
# ---------------------------------------------------------------------------


async def test_dsar_user_bundle(realdb):
    """DSAR for a control-plane User returns their PII + roles + activity."""
    users = realdb.info("a").users
    # Seed an audit row authored by the admin so activity count > 0.
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    async with tenant_mk() as s:
        s.add(
            AuditLog(
                correlation_id=uuid.uuid4(),
                organization_id=org_id,
                actor_id=users["admin"],
                action="invoice.approved",
                entity_type="invoice",
                entity_id=uuid.uuid4(),
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/privacy/dsar",
            json={"subject_type": "user", "identifier": "admin@pytesta.test"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject_type"] == "user"
    assert body["data"]["profile"]["email"] == "admin@pytesta.test"
    assert "admin" in body["data"]["roles"]
    assert body["data"]["activity"]["audit_actions_authored"] >= 1


async def test_dsar_vendor_contact_bundle(realdb):
    """DSAR for a vendor_contact returns vendor PII + related invoices/payments."""
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id, _, invoice_id = await _seed_vendor_with_records(tenant_mk, org_id)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/privacy/dsar",
            json={"subject_type": "vendor_contact", "identifier": str(vendor_id)},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["vendor"]["tax_id"] == "12-3456789"
    assert data["vendor"]["bank_details"]["account"] == "000111222"
    assert len(data["related_invoices"]) == 1
    assert data["related_invoices"][0]["amount"] == "4200.50"  # Decimal-as-string
    assert len(data["related_payments"]) == 1
    assert data["counts"]["portal_users"] == 1


async def test_dsar_records_request_and_audits(realdb):
    """The DSAR request is recorded PII-free + writes a privacy.dsar_export audit
    row carrying only the subject UUID + type."""
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id, _, _ = await _seed_vendor_with_records(tenant_mk, org_id)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/privacy/dsar",
            json={"subject_type": "vendor_contact", "identifier": str(vendor_id)},
        )
    assert resp.status_code == 200

    async with tenant_mk() as s:
        reqs = (
            (
                await s.execute(
                    select(DataSubjectRequest).where(DataSubjectRequest.organization_id == org_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(reqs) == 1
        assert reqs[0].request_type == "dsar_export"
        assert reqs[0].subject_id == vendor_id
        # PII-free: the email / tax_id never landed in the request row.
        assert "acmesupplies" not in (reqs[0].note or "")

        audit = (
            (await s.execute(select(AuditLog).where(AuditLog.action == "privacy.dsar_export")))
            .scalars()
            .all()
        )
        assert len(audit) == 1
        details = audit[0].details
        assert details["subject_id"] == str(vendor_id)
        # No raw PII in the audit details.
        assert "tax_id" not in str(details)
        assert "acmesupplies" not in str(details)


async def test_dsar_vendor_user_bundle(realdb):
    """DSAR for a vendor_user (supplier-portal login) returns their profile PII,
    resolved by email and scoped to the tenant."""
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    _, vendor_user_id, _ = await _seed_vendor_with_records(tenant_mk, org_id)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/privacy/dsar",
            json={"subject_type": "vendor_user", "identifier": "portal@acmesupplies.test"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["subject_type"] == "vendor_user"
    assert body["subject_id"] == str(vendor_user_id)
    assert body["data"]["profile"]["email"] == "portal@acmesupplies.test"
    assert body["data"]["profile"]["full_name"] == "Portal Person"


async def test_dsar_unknown_subject_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/privacy/dsar",
            json={"subject_type": "user", "identifier": "nobody@nowhere.test"},
        )
    assert resp.status_code == 404


async def test_dsar_non_admin_forbidden(realdb):
    """A non-admin (ap_clerk) is denied — the privacy surface is admin-only."""
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(
            "/api/privacy/dsar",
            json={"subject_type": "user", "identifier": "admin@pytesta.test"},
        )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


async def test_erasure_redacts_vendor_contact_preserves_money(realdb):
    """Erasing a vendor_contact redacts every contact PII field but leaves the
    invoice/payment amounts + statuses and the audit_log untouched."""
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id, vendor_user_id, invoice_id = await _seed_vendor_with_records(tenant_mk, org_id)

    # Snapshot the audit_log count + the invoice amount before erasure.
    async with tenant_mk() as s:
        audit_before = (
            (await s.execute(select(AuditLog).where(AuditLog.organization_id == org_id)))
            .scalars()
            .all()
        )
        audit_count_before = len(audit_before)
        inv_amount_before = (
            await s.execute(select(Invoice.amount).where(Invoice.id == invoice_id))
        ).scalar_one()

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/privacy/erasure",
            json={
                "subject_type": "vendor_contact",
                "identifier": str(vendor_id),
                "confirm": True,
                "note": "GDPR request #42",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["fields_redacted"] == 6

    async with tenant_mk() as s:
        vendor = await s.get(Vendor, vendor_id)
        # Every contact PII field redacted.
        assert vendor.email is None
        assert vendor.phone is None
        assert vendor.address is None
        assert vendor.tax_id is None
        assert vendor.bank_details is None
        assert vendor.beneficial_owner_data is None
        # Legal payee preserved (load-bearing on the invoice money trail).
        assert vendor.name == "Acme Supplies Ltd"

        # Portal user redacted too.
        vu = await s.get(VendorUser, vendor_user_id)
        assert vu.full_name == "[redacted]"
        assert vu.email.endswith("@redacted.invalid")
        assert vu.is_active is False

        # MONEY TRAIL UNTOUCHED.
        inv = await s.get(Invoice, invoice_id)
        assert inv.amount == inv_amount_before
        assert inv.vendor_name == "Acme Supplies Ltd"
        assert str(inv.status) in ("approved", "InvoiceStatus.approved")
        pay_amount = (
            await s.execute(select(Payment.amount).where(Payment.invoice_id == invoice_id))
        ).scalar_one()
        assert pay_amount == Decimal("4200.50")

        # AUDIT LOG: append-only — the prior rows are intact, and a NEW
        # privacy.erasure row was added (count strictly increased).
        audit_after = (
            (await s.execute(select(AuditLog).where(AuditLog.organization_id == org_id)))
            .scalars()
            .all()
        )
        assert len(audit_after) == audit_count_before + 1
        assert any(a.action == "privacy.erasure" for a in audit_after)


async def test_erasure_user_redacts_pii(realdb):
    """Erasing a control-plane user redacts email/full_name/sso + deactivates."""
    ctrl_mk = realdb.control_sessionmaker()
    org_id = realdb.info("a").org_id

    # Add a fresh disposable user to erase (don't nuke the seeded admin).
    from app.models.user import User

    target_id = uuid.uuid4()
    async with ctrl_mk() as s:
        s.add(
            User(
                id=target_id,
                email="erase-me@pytesta.test",
                full_name="Erase Me",
                hashed_password="x",
                sso_provider="okta",
                sso_provider_id="okta|123",
                is_active=True,
                organization_id=org_id,
                must_change_password=False,
            )
        )
        await s.commit()

    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.post(
                "/api/privacy/erasure",
                json={
                    "subject_type": "user",
                    "identifier": "erase-me@pytesta.test",
                    "confirm": True,
                },
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "completed"

        async with ctrl_mk() as s:
            u = await s.get(User, target_id)
            assert u.full_name == "[redacted]"
            assert u.email.endswith("@redacted.invalid")
            assert u.sso_provider is None
            assert u.sso_provider_id is None
            assert u.is_active is False
            assert u.hashed_password is None
            # Identity preserved for the audit/financial link.
            assert u.organization_id == org_id
    finally:
        from app.models.user import User as U

        async with ctrl_mk() as s:
            await s.execute(U.__table__.delete().where(U.id == target_id))
            await s.commit()


async def test_erasure_is_idempotent(realdb):
    """Re-running erasure on an already-erased subject is a safe noop."""
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id, _, _ = await _seed_vendor_with_records(tenant_mk, org_id, with_portal=False)

    async with realdb.client(key="a", role="admin") as c:
        first = await c.post(
            "/api/privacy/erasure",
            json={"subject_type": "vendor_contact", "identifier": str(vendor_id), "confirm": True},
        )
        second = await c.post(
            "/api/privacy/erasure",
            json={"subject_type": "vendor_contact", "identifier": str(vendor_id), "confirm": True},
        )
    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert second.status_code == 200
    assert second.json()["status"] == "noop"
    assert second.json()["already_erased"] is True


async def test_erasure_vendor_user_redacts_pii_and_idempotent(realdb):
    """Erasing a vendor_user redacts email/full_name, nulls the credential +
    MFA secret, deactivates — and a re-run is a safe noop."""
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    _, vendor_user_id, _ = await _seed_vendor_with_records(tenant_mk, org_id)

    async with realdb.client(key="a", role="admin") as c:
        first = await c.post(
            "/api/privacy/erasure",
            json={
                "subject_type": "vendor_user",
                "identifier": "portal@acmesupplies.test",
                "confirm": True,
            },
        )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "completed"

    async with tenant_mk() as s:
        vu = await s.get(VendorUser, vendor_user_id)
        assert vu.full_name == "[redacted]"
        assert vu.email.endswith("@redacted.invalid")
        assert vu.hashed_password is None
        assert vu.mfa_secret is None
        assert vu.is_active is False

    # Re-running by the (now tombstoned) email no longer resolves — the subject
    # has been erased — so a second attempt is a clean 404, not a re-redaction.
    async with realdb.client(key="a", role="admin") as c:
        second = await c.post(
            "/api/privacy/erasure",
            json={
                "subject_type": "vendor_user",
                "identifier": "portal@acmesupplies.test",
                "confirm": True,
            },
        )
    assert second.status_code == 404


async def test_erasure_vendor_user_tenant_isolation(realdb):
    """A vendor_user in tenant A is NOT erasable when acting as tenant B — the
    tenant-A portal login stays intact."""
    tenant_mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    _, vendor_user_id, _ = await _seed_vendor_with_records(tenant_mk_a, org_a)

    async with realdb.client(key="b", role="admin") as c:
        resp = await c.post(
            "/api/privacy/erasure",
            json={
                "subject_type": "vendor_user",
                "identifier": "portal@acmesupplies.test",
                "confirm": True,
            },
        )
    assert resp.status_code == 404

    async with tenant_mk_a() as s:
        vu = await s.get(VendorUser, vendor_user_id)
        assert vu.email == "portal@acmesupplies.test"
        assert vu.full_name == "Portal Person"
        assert vu.is_active is True


async def test_erasure_requires_confirm(realdb):
    tenant_mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    vendor_id, _, _ = await _seed_vendor_with_records(tenant_mk, org_id, with_portal=False)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/privacy/erasure",
            json={"subject_type": "vendor_contact", "identifier": str(vendor_id), "confirm": False},
        )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


async def test_dsar_tenant_isolation(realdb):
    """A vendor in tenant A is NOT exportable when acting as tenant B."""
    tenant_mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    vendor_id, _, _ = await _seed_vendor_with_records(tenant_mk_a, org_a, with_portal=False)

    # Acting as tenant B, the tenant-A vendor id must resolve to nothing.
    async with realdb.client(key="b", role="admin") as c:
        resp = await c.post(
            "/api/privacy/dsar",
            json={"subject_type": "vendor_contact", "identifier": str(vendor_id)},
        )
    assert resp.status_code == 404


async def test_erasure_tenant_isolation(realdb):
    """A vendor in tenant A is NOT erasable when acting as tenant B — the
    tenant-A row stays fully intact."""
    tenant_mk_a = realdb.sessionmaker("a")
    org_a = realdb.info("a").org_id
    vendor_id, _, _ = await _seed_vendor_with_records(tenant_mk_a, org_a, with_portal=False)

    async with realdb.client(key="b", role="admin") as c:
        resp = await c.post(
            "/api/privacy/erasure",
            json={"subject_type": "vendor_contact", "identifier": str(vendor_id), "confirm": True},
        )
    assert resp.status_code == 404

    # Tenant A's vendor PII is untouched.
    async with tenant_mk_a() as s:
        vendor = await s.get(Vendor, vendor_id)
        assert vendor.email == "contact@acmesupplies.test"
        assert vendor.tax_id == "12-3456789"


async def test_dsar_user_cross_org_not_resolved(realdb):
    """A user belonging to org A is not exportable when acting as tenant B —
    resolve_subject_id filters by organization_id."""
    # Tenant B admin asks for tenant A's admin email.
    async with realdb.client(key="b", role="admin") as c:
        resp = await c.post(
            "/api/privacy/dsar",
            json={"subject_type": "user", "identifier": "admin@pytesta.test"},
        )
    assert resp.status_code == 404
