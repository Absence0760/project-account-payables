"""Real-Postgres coverage for `POST /api/invoices/{id}/complete`'s
amount-floor auto-approve path — specifically the money-threshold DISPLAY,
not just the decision.

Found via the frontend e2e suite after the workflow-schema discriminator fix
(#237) started correctly typing `ApprovalStepConfig.auto_approve_below` as
`Decimal` and the JSONB-write fix (workflow_definitions.py) started
serializing it as an exact string rather than a raw Decimal object. Both
fixes are individually correct (and covered elsewhere), but
`api/workflow.py::complete_invoice` read the threshold straight off the
frozen `steps_config_snapshot` and formatted it with an `f"{auto_below:,.2f}"`
spec that assumes a float/Decimal — a string threshold (which the JSONB now
legitimately stores) raises `ValueError: Unknown format code 'f' for object
of type 'str'`, turning a successful auto-approve into a 500.

`decide_auto_approve` itself was never affected — it already coerces via
`Decimal(str(auto_below))` before comparing. Only the success-message
f-string was unguarded. This is exactly the class of bug the near-identical
comment in `services/review.py` (`_enforce_approval_thresholds`) already
anticipates and guards against for `max_invoice_amount` / `require_cfo_above`
— `complete_invoice`'s own display line was the one spot that didn't.
"""

from __future__ import annotations

from decimal import Decimal

from app.models.invoice import Invoice, InvoiceStatus

TENANT = "a"


async def _set_auto_approve_below(realdb, *, workflow_id: str, threshold: str):
    async with realdb.client(key=TENANT, role="admin") as c:
        wf = (await c.get(f"/api/workflows/{workflow_id}")).json()
        steps = wf["steps_config"]["steps"]
        for step in steps:
            if step["type"] == "approval":
                step["enabled"] = True
                step["config"] = {
                    **step.get("config", {}),
                    "required": True,
                    "auto_approve_below": threshold,
                }
        resp = await c.patch(f"/api/workflows/{workflow_id}", json={"steps": steps})
        assert resp.status_code == 200, resp.text
        return steps


async def _get_active_workflow_id(realdb) -> str:
    async with realdb.client(key=TENANT, role="admin") as c:
        wfs = (await c.get("/api/workflows")).json()["items"]
    active = next(w for w in wfs if w["is_active"])
    return active["id"]


async def test_complete_auto_approves_and_formats_the_threshold_message(realdb):
    """The exact CI repro: PATCH the active workflow's approval step with a
    string auto_approve_below (mirrors what the JSONB now stores post-write),
    create an invoice under it as a DIFFERENT actor (segregation must not
    degrade this to human review), complete it, and assert a 200 with a
    correctly formatted money message — not a 500."""
    workflow_id = await _get_active_workflow_id(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        original_steps = (await c.get(f"/api/workflows/{workflow_id}")).json()["steps_config"][
            "steps"
        ]

    try:
        await _set_auto_approve_below(realdb, workflow_id=workflow_id, threshold="5000.00")

        # Created by ap_manager, completed by admin — different actors, so the
        # segregation-of-duties degrade doesn't mask the auto-approve path.
        async with realdb.client(key=TENANT, role="ap_manager") as c:
            create_resp = await c.post(
                "/api/invoices",
                json={
                    "vendor": "Auto Approve Vendor",
                    "invoice_number": f"AA-{workflow_id[:8]}",
                    "amount": "1234.50",
                    "currency": "USD",
                },
            )
        assert create_resp.status_code == 201, create_resp.text
        invoice_id = create_resp.json()["id"]

        async with realdb.client(key=TENANT, role="admin") as c:
            complete_resp = await c.post(f"/api/invoices/{invoice_id}/complete")
        assert complete_resp.status_code == 200, complete_resp.text
        body = complete_resp.json()
        assert body["status"] == "approved"
        assert "$5,000.00" in body["message"]

        mk = realdb.sessionmaker(TENANT)
        async with mk() as s:
            inv = await s.get(Invoice, invoice_id)
            assert inv.status == InvoiceStatus.approved
            assert inv.amount == Decimal("1234.50")
    finally:
        async with realdb.client(key=TENANT, role="admin") as c:
            await c.patch(f"/api/workflows/{workflow_id}", json={"steps": original_steps})
