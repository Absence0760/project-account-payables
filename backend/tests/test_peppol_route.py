"""Route tests for POST /api/invoices/{id}/peppol-send (real-Postgres + ASGI).

Covers the happy path, the idempotent re-send (already_sent + no double
transmit), 404 / 400 / 422 error mapping with PII-free bodies, and RBAC
(ap_clerk is rejected). Auth-gating itself is covered by test_rbac.py — this
route is on neither PUBLIC_BY_DESIGN nor ALTERNATE_AUTH.
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
                due_date=date(2026, 1, 31),
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


@pytest.mark.asyncio
async def test_peppol_send_422_body_is_structured_and_names_its_rules(realdb):
    """The tax/conformance 422 returns the FULL error — field, code and the
    PII-free human sentence — not the old `"field: code"` join.

    Two things ride on this. The prose means a client no longer has to keep its
    own code→prose map to say anything actionable. The EN 16931 / PEPPOL rule id
    is what the RECEIVER's own validator names, so it has to survive both as the
    machine-readable `type` and inside `msg` (a client that flattens the list to
    a string keeps only `loc` + `msg`).
    """
    mk = realdb.sessionmaker("a")
    # A malformed DE VAT id fails the tax pass; sending also runs the BIS
    # Billing 3.0 conformance pass, which reports rule ids as the code.
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id, vendor_tax_id="DE12")
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert isinstance(detail, list) and detail

    for item in detail:
        assert item["loc"] and isinstance(item["loc"][0], str)
        assert item["type"]
        assert item["msg"].strip(), "the human half must not be empty"
        # A rule-id code also names itself in the message.
        if item["type"].upper() == item["type"] and item["type"].startswith(("BR-", "PEPPOL-")):
            assert item["type"] in item["msg"]

    fields = {item["loc"][0] for item in detail}
    assert "seller.tax_id" in fields
    # PII-free: the malformed value never appears anywhere in the body.
    assert "DE12" not in resp.text


# --------------------------------------------------------------------------
# GET /api/invoices/{id}/peppol-transmissions
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_transmissions_empty_before_any_send(realdb):
    """An invoice never transmitted answers with an empty list — a real "not
    yet sent", which the send response alone could never provide."""
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/peppol-transmissions")
    assert resp.status_code == 200
    assert resp.json() == {"transmissions": []}


@pytest.mark.asyncio
async def test_transmissions_reports_the_send_and_is_pii_free(realdb):
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)
    async with realdb.client(key="a", role="admin") as c:
        sent = await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)
        assert sent.status_code == 200, sent.text
        resp = await c.get(f"/api/invoices/{inv_id}/peppol-transmissions")

    assert resp.status_code == 200
    rows = resp.json()["transmissions"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == sent.json()["transmission_id"]
    assert row["direction"] == "outbound"
    assert row["status"] == "sent"
    assert row["message_id"] == sent.json()["message_id"]
    assert row["participant_scheme"] == "9930"
    assert row["created_at"]

    # PII-free: the counterparty's and our own registered ids live on the row
    # and inside the UBL, but never in this response.
    assert "SUPPLIER123" not in resp.text
    assert "DE000000000" not in resp.text
    assert "participant_value" not in resp.text
    assert "sender_value" not in resp.text


@pytest.mark.asyncio
async def test_transmissions_readable_by_ap_clerk(realdb):
    """Read-gated like `GET /{id}/einvoice` (ALL_ROLES) — the same subject,
    read-only and PII-free. Only the network-touching POST stays narrow."""
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/peppol-transmissions")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_transmissions_unknown_invoice_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/invoices/{uuid.uuid4()}/peppol-transmissions")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transmissions_are_tenant_scoped(realdb):
    """Tenant B cannot read tenant A's invoice's transmissions — the id is
    resolved through `get_tenant_db`, so it simply isn't there (opaque 404)."""
    mk = realdb.sessionmaker("a")
    inv_id = await _seed_invoice(mk, realdb.info("a").org_id)
    async with realdb.client(key="a", role="admin") as c:
        assert (await c.post(f"/api/invoices/{inv_id}/peppol-send", json=_BODY)).status_code == 200

    async with realdb.client(key="b", role="admin") as c:
        resp = await c.get(f"/api/invoices/{inv_id}/peppol-transmissions")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_transmissions_are_entity_scoped(realdb):
    """`X-Entity-ID` narrows the invoice lookup like every other entity-scoped
    read, so a subsidiary's selection cannot surface another entity's row."""
    from app.models.entity import Entity

    mk = realdb.sessionmaker("a")
    org_id = realdb.info("a").org_id
    inv_id = await _seed_invoice(mk, org_id)

    async with mk() as s:
        other = Entity(
            organization_id=org_id,
            name="Other Sub",
            slug=f"other-{uuid.uuid4().hex[:8]}",
            is_default=False,
        )
        s.add(other)
        await s.commit()
        other_id = str(other.id)

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(
            f"/api/invoices/{inv_id}/peppol-transmissions",
            headers={"X-Entity-ID": other_id},
        )
    assert resp.status_code == 404
