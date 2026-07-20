"""Standalone `POST /api/payments` enforces the `cfo_approval_above` gate.

The run-based path (`create_payment_run`) has always computed
`requires_cfo_approval` off the org's `payments.cfo_approval_above` threshold
and refused `/execute` until a CFO signs off. The standalone single-invoice
path had no equivalent check at all — any actor holding `payment.execute`
could book an above-threshold payment directly, a structural gap in the CFO
sign-off invariant (issue #129).

A standalone payment has no separate `/execute` step to gate the way a run
does (`requires_cfo_approval` lives on `PaymentRun`, not `Payment`), so the
fix gates at CREATION time instead: an above-threshold amount requires the
creating actor to hold the CFO role, or the request is refused (403) and the
caller is pointed at the payment-run flow. A misconfigured (non-numeric)
threshold fails CLOSED — every standalone payment then requires a CFO,
mirroring `create_payment_run`'s identical fail-closed handling of the same
setting.

All DB-backed via `realdb` (requires the dev Postgres; skips otherwise).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.organization import Organization

TENANT = "a"


async def _default_entity_id(s):
    from app.models.entity import Entity

    return (await s.execute(select(Entity.id).where(Entity.is_default))).scalar_one()


async def _seed_approved_invoice(mk, org_id, amount: Decimal) -> uuid.UUID:
    async with mk() as s:
        ent = await _default_entity_id(s)
        inv = Invoice(
            organization_id=org_id,
            entity_id=ent,
            invoice_number=f"CFOGATE-{uuid.uuid4().hex[:8]}",
            vendor_name="CFO Gate Vendor",
            amount=amount,
            currency="USD",
            status=InvoiceStatus.approved,
        )
        s.add(inv)
        await s.commit()
        return inv.id


async def _set_cfo_threshold(realdb, *, org_id, value) -> None:
    ctrl_mk = realdb.control_sessionmaker()
    async with ctrl_mk() as s:
        org = await s.get(Organization, org_id)
        settings = dict(org.settings or {})
        payments = dict(settings.get("payments") or {})
        if value is None:
            payments.pop("cfo_approval_above", None)
        else:
            payments["cfo_approval_above"] = value
        settings["payments"] = payments
        org.settings = settings
        await s.commit()


@pytest.mark.asyncio
async def test_above_threshold_non_cfo_actor_is_refused(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    try:
        await _set_cfo_threshold(realdb, org_id=org_id, value="100.00")
        inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500.00"))

        async with realdb.client(key=TENANT, role="admin") as client:
            resp = await client.post(
                "/api/payments",
                json={"invoice_id": str(inv_id), "amount": "500.00", "method": "ach"},
            )
        assert resp.status_code == 403, resp.text
        assert "CFO" in resp.json()["detail"]
    finally:
        await _set_cfo_threshold(realdb, org_id=org_id, value=None)


@pytest.mark.asyncio
async def test_above_threshold_cfo_actor_succeeds(realdb):
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    try:
        await _set_cfo_threshold(realdb, org_id=org_id, value="100.00")
        inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500.00"))

        async with realdb.client(key=TENANT, role="cfo") as client:
            resp = await client.post(
                "/api/payments",
                json={"invoice_id": str(inv_id), "amount": "500.00", "method": "ach"},
            )
        assert resp.status_code == 201, resp.text
    finally:
        await _set_cfo_threshold(realdb, org_id=org_id, value=None)


@pytest.mark.asyncio
async def test_below_threshold_non_cfo_actor_unaffected(realdb):
    """A payment under the threshold is unaffected — no CFO needed."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    try:
        await _set_cfo_threshold(realdb, org_id=org_id, value="1000.00")
        inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500.00"))

        async with realdb.client(key=TENANT, role="admin") as client:
            resp = await client.post(
                "/api/payments",
                json={"invoice_id": str(inv_id), "amount": "500.00", "method": "ach"},
            )
        assert resp.status_code == 201, resp.text
    finally:
        await _set_cfo_threshold(realdb, org_id=org_id, value=None)


@pytest.mark.asyncio
async def test_zero_threshold_means_no_gate(realdb):
    """A threshold of 0 (or unset) means 'no gate' — matches create_payment_run."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    try:
        await _set_cfo_threshold(realdb, org_id=org_id, value="0")
        inv_id = await _seed_approved_invoice(mk, org_id, Decimal("500000.00"))

        async with realdb.client(key=TENANT, role="admin") as client:
            resp = await client.post(
                "/api/payments",
                json={"invoice_id": str(inv_id), "amount": "500000.00", "method": "ach"},
            )
        assert resp.status_code == 201, resp.text
    finally:
        await _set_cfo_threshold(realdb, org_id=org_id, value=None)


@pytest.mark.asyncio
async def test_malformed_threshold_fails_closed_for_non_cfo(realdb):
    """A corrupted/unparseable threshold must not silently disable the gate —
    every standalone payment then requires a CFO (fail closed), same as
    `create_payment_run`'s handling of the identical setting."""
    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    try:
        await _set_cfo_threshold(realdb, org_id=org_id, value="not-a-number")
        inv_id = await _seed_approved_invoice(mk, org_id, Decimal("10.00"))

        async with realdb.client(key=TENANT, role="admin") as client:
            resp = await client.post(
                "/api/payments",
                json={"invoice_id": str(inv_id), "amount": "10.00", "method": "ach"},
            )
        assert resp.status_code == 403, resp.text

        # A CFO can still create it.
        async with realdb.client(key=TENANT, role="cfo") as client:
            resp = await client.post(
                "/api/payments",
                json={"invoice_id": str(inv_id), "amount": "10.00", "method": "ach"},
            )
        assert resp.status_code == 201, resp.text
    finally:
        await _set_cfo_threshold(realdb, org_id=org_id, value=None)
