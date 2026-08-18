"""The unattended auto-approve path must honour the structuring aggregate.

`review._enforce_approval_thresholds` evaluates `max_invoice_amount` and
`require_cfo_above` against `invoice.amount + vendor_recent_spend(...)` — the
guard added to stop one payable being split into several under-threshold
invoices. `extraction.decide_auto_approve` evaluated the same two gates against
THIS invoice's amount alone, so the split-payable bypass survived on the path
with no human in it at all: each piece auto-approved past the max-amount cap and
the CFO gate with nobody ever looking.

`auto_approve_below` deliberately stays on the single-invoice amount — it is a
"too small to be worth a human's time" convenience, not a spend control, and
aggregating it would silently stop it firing for any frequent vendor.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.services.extraction import decide_auto_approve

TENANT = "a"

_EXT = {"auto_approve_enabled": True, "auto_approve_threshold": 0.9}


# ---------------------------------------------------------------------------
# decide_auto_approve — pure
# ---------------------------------------------------------------------------


def test_aggregate_over_max_amount_revokes_auto_approve():
    cfg = {"max_invoice_amount": "5000"}
    # This invoice alone clears the cap...
    assert decide_auto_approve(_EXT, cfg, overall_confidence=1.0, amount=Decimal("3000")) is True
    # ...but the same vendor's recent spend pushes the aggregate over it.
    assert (
        decide_auto_approve(
            _EXT,
            cfg,
            overall_confidence=1.0,
            amount=Decimal("3000"),
            aggregate_amount=Decimal("6000"),
        )
        is False
    )


def test_aggregate_over_cfo_threshold_revokes_auto_approve():
    cfg = {"require_cfo_above": "5000"}
    assert decide_auto_approve(_EXT, cfg, overall_confidence=1.0, amount=Decimal("3000")) is True
    assert (
        decide_auto_approve(
            _EXT,
            cfg,
            overall_confidence=1.0,
            amount=Decimal("3000"),
            aggregate_amount=Decimal("5000.01"),
        )
        is False
    )


def test_aggregate_at_the_threshold_boundary_does_not_revoke():
    """`max_invoice_amount` trips on strictly-greater, `require_cfo_above` on
    strictly-greater — an aggregate landing exactly on either is still clear."""
    assert (
        decide_auto_approve(
            _EXT,
            {"max_invoice_amount": "5000", "require_cfo_above": "5000"},
            overall_confidence=1.0,
            amount=Decimal("3000"),
            aggregate_amount=Decimal("5000"),
        )
        is True
    )


def test_auto_approve_below_still_measures_the_single_invoice():
    """A high aggregate must not stop the small-invoice floor from firing when
    no money-control gate is configured."""
    assert (
        decide_auto_approve(
            {},
            {"auto_approve_below": "500"},
            overall_confidence=0.0,
            amount=Decimal("100"),
            aggregate_amount=Decimal("99999"),
        )
        is True
    )


def test_aggregate_none_preserves_single_invoice_behaviour():
    cfg = {"max_invoice_amount": "5000"}
    assert (
        decide_auto_approve(
            _EXT, cfg, overall_confidence=1.0, amount=Decimal("6000"), aggregate_amount=None
        )
        is False
    )
    assert (
        decide_auto_approve(
            _EXT, cfg, overall_confidence=1.0, amount=Decimal("4000"), aggregate_amount=None
        )
        is True
    )


# ---------------------------------------------------------------------------
# resolve_gate_aggregate + the /complete wiring — real Postgres
# ---------------------------------------------------------------------------


async def _active_workflow_id(realdb) -> str:
    async with realdb.client(key=TENANT, role="admin") as c:
        wfs = (await c.get("/api/workflows")).json()["items"]
    return next(w["id"] for w in wfs if w["is_active"])


async def _set_approval_config(realdb, *, workflow_id: str, config: dict) -> list:
    async with realdb.client(key=TENANT, role="admin") as c:
        wf = (await c.get(f"/api/workflows/{workflow_id}")).json()
        original = wf["steps_config"]["steps"]
        steps = [dict(s) for s in original]
        for step in steps:
            if step["type"] == "approval":
                step["enabled"] = True
                step["config"] = {**(step.get("config") or {}), "required": True, **config}
        resp = await c.patch(f"/api/workflows/{workflow_id}", json={"steps": steps})
        assert resp.status_code == 200, resp.text
    return original


async def test_resolve_gate_aggregate_sums_the_vendors_recent_spend(realdb):
    from app.services.extraction import resolve_gate_aggregate

    org_id = realdb.info(TENANT).org_id
    mk = realdb.sessionmaker(TENANT)
    vendor_id = uuid.uuid4()
    async with mk() as s:
        from app.models.vendor import Vendor

        s.add(Vendor(id=vendor_id, organization_id=org_id, name="Aggregate Vendor"))
        for n, amt in (("AGG-1", "3000.00"), ("AGG-2", "2500.00")):
            s.add(
                Invoice(
                    organization_id=org_id,
                    vendor_id=vendor_id,
                    invoice_number=f"{n}-{uuid.uuid4().hex[:6]}",
                    vendor_name="Aggregate Vendor",
                    amount=Decimal(amt),
                    currency="USD",
                    status=InvoiceStatus.approved,
                )
            )
        target = Invoice(
            organization_id=org_id,
            vendor_id=vendor_id,
            invoice_number=f"AGG-3-{uuid.uuid4().hex[:6]}",
            vendor_name="Aggregate Vendor",
            amount=Decimal("1000.00"),
            currency="USD",
            status=InvoiceStatus.new,
        )
        s.add(target)
        await s.commit()

        aggregate = await resolve_gate_aggregate(s, target, org_settings={})
        assert aggregate == Decimal("6500.00")

        # Disabling the rule degrades to the single-invoice amount.
        off = await resolve_gate_aggregate(
            s, target, org_settings={"fraud_rules": {"structuring_enabled": False}}
        )
        assert off == Decimal("1000.00")


async def test_split_payable_cannot_auto_approve_past_the_cfo_gate(realdb):
    """Two 3,000 invoices from one vendor under a 5,000 CFO gate: the first
    auto-approves, the second must fall back to human review because the
    aggregate (6,000) trips the gate a reviewer would have been stopped by."""
    workflow_id = await _active_workflow_id(realdb)
    original = await _set_approval_config(
        realdb,
        workflow_id=workflow_id,
        config={"auto_approve_below": "5000", "require_cfo_above": "5000"},
    )
    vendor_name = f"Structuring Vendor {uuid.uuid4().hex[:6]}"
    try:
        ids: list[str] = []
        for n in (1, 2):
            async with realdb.client(key=TENANT, role="ap_manager") as c:
                created = await c.post(
                    "/api/invoices",
                    json={
                        "vendor": vendor_name,
                        "invoice_number": f"SPLIT-{n}-{uuid.uuid4().hex[:6]}",
                        "amount": "3000.00",
                        "currency": "USD",
                    },
                )
            assert created.status_code == 201, created.text
            ids.append(created.json()["id"])
            async with realdb.client(key=TENANT, role="admin") as c:
                done = await c.post(f"/api/invoices/{ids[-1]}/complete")
            assert done.status_code == 200, done.text

        mk = realdb.sessionmaker(TENANT)
        uuids = [uuid.UUID(i) for i in ids]
        async with mk() as s:
            found = (await s.execute(select(Invoice).where(Invoice.id.in_(uuids)))).scalars().all()
            rows = {str(i.id): i.status for i in found}
        # Both invoices were linked to the same vendor, so the second sees the
        # first in its aggregate.
        assert rows[ids[0]] == InvoiceStatus.approved
        assert rows[ids[1]] == InvoiceStatus.ready_for_review
    finally:
        async with realdb.client(key=TENANT, role="admin") as c:
            await c.patch(f"/api/workflows/{workflow_id}", json={"steps": original})
