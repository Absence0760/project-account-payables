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


async def _set_extraction_confidence_bar(realdb, *, workflow_id: str, threshold: float):
    """Turn the extraction step's confidence auto-approve ON with the given bar,
    and leave the approval step with NO amount floor."""
    async with realdb.client(key=TENANT, role="admin") as c:
        wf = (await c.get(f"/api/workflows/{workflow_id}")).json()
        steps = wf["steps_config"]["steps"]
        for step in steps:
            if step["type"] == "extraction":
                step["enabled"] = True
                step["config"] = {
                    "auto_approve_enabled": True,
                    "auto_approve_threshold": threshold,
                }
            if step["type"] == "approval":
                step["enabled"] = True
                cfg = {**step.get("config", {}), "required": True}
                cfg.pop("auto_approve_below", None)
                step["config"] = cfg
        resp = await c.patch(f"/api/workflows/{workflow_id}", json={"steps": steps})
        assert resp.status_code == 200, resp.text


async def test_complete_never_auto_approves_on_the_extraction_confidence_bar(realdb):
    """`complete_invoice` hands `decide_auto_approve` a hardcoded
    `overall_confidence=0.0` — a sentinel meaning "there is no extraction result
    on this path", not a measurement. It also used to hand it the extraction
    step's config, and `auto_approve_threshold` is a schema-valid `0.0..1.0`
    float, so an org that dragged the confidence bar to `0` (meaning
    "auto-approve at any confidence", for the EXTRACTION path) made
    `0.0 >= 0.0` fire here instead: a $999,999 invoice auto-approved with **no
    amount floor configured at all**, stamped `reason: "below_threshold"` with a
    null threshold, and then 500-ing on the response message — *after* the
    commit, so the caller saw an error while the invoice was silently approved.

    The amount floor is the only trigger that means anything on this path."""
    workflow_id = await _get_active_workflow_id(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        original_steps = (await c.get(f"/api/workflows/{workflow_id}")).json()["steps_config"][
            "steps"
        ]

    try:
        await _set_extraction_confidence_bar(realdb, workflow_id=workflow_id, threshold=0.0)

        async with realdb.client(key=TENANT, role="ap_manager") as c:
            create_resp = await c.post(
                "/api/invoices",
                json={
                    "vendor": "No Floor Vendor",
                    "invoice_number": f"NOFLOOR-{workflow_id[:8]}",
                    "amount": "999999.00",
                    "currency": "USD",
                },
            )
        assert create_resp.status_code == 201, create_resp.text
        invoice_id = create_resp.json()["id"]

        async with realdb.client(key=TENANT, role="admin") as c:
            complete_resp = await c.post(f"/api/invoices/{invoice_id}/complete")
        # Not a 500, and not an auto-approval — it goes to a human.
        assert complete_resp.status_code == 200, complete_resp.text
        assert complete_resp.json()["status"] == "ready_for_review"

        mk = realdb.sessionmaker(TENANT)
        async with mk() as s:
            inv = await s.get(Invoice, invoice_id)
            assert inv.status == InvoiceStatus.ready_for_review
            assert inv.approved_by is None
    finally:
        async with realdb.client(key=TENANT, role="admin") as c:
            await c.patch(f"/api/workflows/{workflow_id}", json={"steps": original_steps})


async def test_complete_audit_row_records_the_threshold_as_an_exact_string(realdb):
    """The `invoice.auto_approved` row's `threshold` is the parsed floor as an
    exact decimal string — the same figure the comparison and the response
    message use, never a raw JSONB value or a null."""
    from sqlalchemy import select

    from app.models.workflow import AuditLog

    workflow_id = await _get_active_workflow_id(realdb)
    async with realdb.client(key=TENANT, role="admin") as c:
        original_steps = (await c.get(f"/api/workflows/{workflow_id}")).json()["steps_config"][
            "steps"
        ]

    try:
        await _set_auto_approve_below(realdb, workflow_id=workflow_id, threshold="5000.00")

        async with realdb.client(key=TENANT, role="ap_manager") as c:
            create_resp = await c.post(
                "/api/invoices",
                json={
                    "vendor": "Audit Threshold Vendor",
                    "invoice_number": f"AUDTH-{workflow_id[:8]}",
                    "amount": "100.00",
                    "currency": "USD",
                },
            )
        invoice_id = create_resp.json()["id"]
        async with realdb.client(key=TENANT, role="admin") as c:
            assert (await c.post(f"/api/invoices/{invoice_id}/complete")).status_code == 200

        mk = realdb.sessionmaker(TENANT)
        async with mk() as s:
            row = (
                (
                    await s.execute(
                        select(AuditLog).where(
                            AuditLog.action == "invoice.auto_approved",
                            AuditLog.entity_id == invoice_id,
                        )
                    )
                )
                .scalars()
                .one()
            )
            assert row.details["reason"] == "below_threshold"
            assert row.details["threshold"] == "5000.00"
    finally:
        async with realdb.client(key=TENANT, role="admin") as c:
            await c.patch(f"/api/workflows/{workflow_id}", json={"steps": original_steps})
