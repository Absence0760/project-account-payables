"""Route tests for POST /api/invoices/{id}/peppol-send (real-Postgres + ASGI).

Covers the happy path, the idempotent re-send (already_sent + no double
transmit), 404 / 400 / 422 error mapping with PII-free bodies, and RBAC
(ap_clerk is rejected). Auth-gating itself is covered by test_rbac.py — this
route is NOT in NO_AUTH_REQUIRED.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.models.invoice import Invoice, InvoiceLineItem, InvoiceStatus
from app.models.peppol_transmission import PeppolTransmission


async def _seed_invoice(
    mk, org_id, *, vendor_tax_id="DE123456789", status=InvoiceStatus.approved
) -> uuid.UUID:
    """A BIS Billing 3.0-conformant invoice — see `test_peppol_send._seed_invoice`."""
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                correlation_id=uuid.uuid4(),
                invoice_number=f"INV-{uuid.uuid4().hex[:8]}",
                vendor_name="Acme GmbH",
                vendor_tax_id=vendor_tax_id,
                amount=Decimal("119.00"),
                currency="EUR",
                invoice_date=date(2026, 1, 1),
                subtotal=Decimal("100.00"),
                tax_amount=Decimal("19.00"),
                tax_rate=Decimal("19.00"),
                status=status,
            )
        )
        s.add(
            InvoiceLineItem(
                invoice_id=inv_id,
                line_number=1,
                description="Widget",
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                total=Decimal("100.00"),
                tax=Decimal("19.00"),
            )
        )
        await s.commit()
    return inv_id


@pytest_asyncio.fixture(autouse=True)
async def _company_identity(realdb):
    """Give the tenant a company profile with a country code.

    The buyer party is built from `org.settings["company"]`, and BIS Billing
    3.0 requires the buyer's country (BR-11) — without it the send path refuses
    to transmit a document under a doc-type id that claims the profile.
    """
    from sqlalchemy.orm.attributes import flag_modified

    from app.models.organization import Organization

    org_id = realdb.info("a").org_id
    ctrl = realdb.control_sessionmaker()
    async with ctrl() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        cfg = dict(org.settings or {})
        cfg["company"] = {"name": "Buyer Co", "country_code": "DE"}
        org.settings = cfg
        flag_modified(org, "settings")
        await s.commit()
    yield
    async with ctrl() as s:
        org = (await s.execute(select(Organization).where(Organization.id == org_id))).scalar_one()
        cfg = dict(org.settings or {})
        cfg.pop("company", None)
        org.settings = cfg
        flag_modified(org, "settings")
        await s.commit()


_BODY = {
    "receiver_scheme": "9930",
    "receiver_value": "SUPPLIER123",
    "sender_scheme": "9930",
    "sender_value": "DE000000000",
}


@pytest.mark.asyncio
async def test_peppol_send_happy_path(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)

    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "sent"
    assert body["direction"] == "outbound"
    assert body["already_sent"] is False
    assert body["message_id"]


@pytest.mark.asyncio
async def test_peppol_send_is_idempotent_over_http(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)

    async with realdb.client(key="a", role="admin") as c:
        first = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)
        second = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["already_sent"] is True
    assert second.json()["transmission_id"] == first.json()["transmission_id"]

    async with mk() as s:
        rows = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(PeppolTransmission.invoice_id == inv_id)
            )
        ).scalar_one()
        assert rows == 1


@pytest.mark.asyncio
async def test_peppol_send_unknown_invoice_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{uuid.uuid4()}/peppol-send", json=_BODY)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_peppol_send_malformed_receiver_400(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)
    bad = {**_BODY, "receiver_scheme": "not-digits"}
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=bad)
    assert resp.status_code == 400
    # PII-free: the body names the field, not the value.
    assert "SUPPLIER123" not in resp.text


@pytest.mark.asyncio
async def test_peppol_send_tax_invalid_422_pii_free(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id, vendor_tax_id="DE12")
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)
    assert resp.status_code == 422
    assert "DE12" not in resp.text


@pytest.mark.asyncio
async def test_peppol_send_unregistered_receiver_422(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)
    body = {**_BODY, "receiver_value": "UNREGISTERED-CO"}
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=body)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "receiver_not_registered"


@pytest.mark.asyncio
async def test_peppol_send_rbac_ap_clerk_forbidden(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_peppol_send_non_approved_invoice_422(realdb):
    """A 'new' invoice must not be transmittable — PEPPOL send is gated on AP
    approval like the ERP-send / payment-run paths."""
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id, status=InvoiceStatus.new)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "invoice_not_approved"

    # No transmission row was written for the rejected status.
    async with mk() as s:
        rows = (
            await s.execute(
                select(func.count())
                .select_from(PeppolTransmission)
                .where(PeppolTransmission.invoice_id == inv_id)
            )
        ).scalar_one()
        assert rows == 0


@pytest.mark.asyncio
async def test_peppol_send_missing_sender_400(realdb):
    """No sender id in the body AND none in org settings.peppol → 400."""
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)
    body = {"receiver_scheme": "9930", "receiver_value": "SUPPLIER123"}
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=body)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "sender participant id is not configured"
