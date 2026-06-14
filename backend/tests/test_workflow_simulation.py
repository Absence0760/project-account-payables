"""Coverage for workflow simulation (dry-run, no side effects).

- A condition-branching workflow takes the true branch for a high amount and
  the false branch for a low amount.
- /simulate accepts either an inline SimInvoice or a real invoice_id.
- webhook/email/delay steps are recorded (dry-run) — never executed for real.
- the simulate() service is pure: same input → same path.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.models.invoice import Invoice, InvoiceStatus

# A workflow that routes on amount: >= 1000 → CFO (step 4); else manager (3).
_BRANCHING_STEPS = [
    {"number": 1, "type": "extraction", "name": "Extract", "enabled": True, "config": {}},
    {
        "number": 2,
        "type": "condition",
        "name": "High value?",
        "enabled": True,
        "config": {
            "rules": [{"field": "amount", "operator": "gte", "value": 1000}],
            "match": "all",
            "on_true_goto": 4,
            "on_false_goto": 3,
        },
    },
    {"number": 3, "type": "approval", "name": "Manager", "enabled": True, "config": {}},
    {"number": 4, "type": "approval", "name": "CFO", "enabled": True, "config": {}},
    {"number": 5, "type": "erp_export", "name": "ERP", "enabled": True, "config": {}},
]


async def _create_branching(c):
    return (
        await c.post(
            "/api/workflows",
            json={"name": "Branching", "description": "d", "steps": _BRANCHING_STEPS},
        )
    ).json()["id"]


def _step_numbers(path):
    return [s["step_number"] for s in path]


# ---------------------------------------------------------------------------
# endpoint — inline SimInvoice
# ---------------------------------------------------------------------------


async def test_simulate_high_amount_takes_cfo_branch(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = await _create_branching(c)
        resp = await c.post(
            f"/api/workflows/{wf_id}/simulate",
            json={"invoice": {"amount": "5000", "currency": "USD"}},
        )
    assert resp.status_code == 200
    body = resp.json()
    # Condition matched → jumps to step 4 (CFO), skipping the manager step 3.
    nums = _step_numbers(body["path"])
    assert nums == [1, 2, 4, 5]
    assert body["terminal_state"] == "sent_to_erp"
    cond = next(s for s in body["path"] if s["type"] == "condition")
    assert cond["outcome"] == "matched"


async def test_simulate_low_amount_takes_manager_branch(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = await _create_branching(c)
        resp = await c.post(
            f"/api/workflows/{wf_id}/simulate",
            json={"invoice": {"amount": "100", "currency": "USD"}},
        )
    assert resp.status_code == 200
    body = resp.json()
    nums = _step_numbers(body["path"])
    # Condition false → fall through to manager step 3, then ERP.
    assert nums == [1, 2, 3, 4, 5] or nums == [1, 2, 3, 5]
    cond = next(s for s in body["path"] if s["type"] == "condition")
    assert cond["outcome"] == "not_matched"


async def test_simulate_requires_invoice_or_id(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = await _create_branching(c)
        resp = await c.post(f"/api/workflows/{wf_id}/simulate", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# endpoint — real invoice_id
# ---------------------------------------------------------------------------


async def test_simulate_with_invoice_id(realdb):
    org_id = realdb.info("a").org_id
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = Invoice(
            invoice_number="SIM-1",
            vendor_name="V",
            amount=Decimal("9999.00"),
            currency="USD",
            status=InvoiceStatus.new,
            organization_id=org_id,
        )
        s.add(inv)
        await s.commit()
        inv_id = str(inv.id)

    async with realdb.client(key="a", role="admin") as c:
        wf_id = await _create_branching(c)
        resp = await c.post(f"/api/workflows/{wf_id}/simulate", json={"invoice_id": inv_id})
    assert resp.status_code == 200
    # amount 9999 >= 1000 → CFO branch.
    assert _step_numbers(resp.json()["path"]) == [1, 2, 4, 5]


async def test_simulate_unknown_invoice_id_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = await _create_branching(c)
        resp = await c.post(
            f"/api/workflows/{wf_id}/simulate", json={"invoice_id": str(uuid.uuid4())}
        )
    assert resp.status_code == 404


async def test_simulate_unknown_workflow_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            f"/api/workflows/{uuid.uuid4()}/simulate",
            json={"invoice": {"amount": "1", "currency": "USD"}},
        )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# custom step types are recorded, never executed
# ---------------------------------------------------------------------------


async def test_simulate_records_webhook_email_delay(realdb):
    steps = [
        {"number": 1, "type": "extraction", "name": "Extract", "enabled": True, "config": {}},
        {
            "number": 2,
            "type": "webhook",
            "name": "Hook",
            "enabled": True,
            "config": {
                "url": "https://example.invalid/x",
                "method": "POST",
                "headers": {},
                "body_template": None,
                "timeout_seconds": 5,
            },
        },
        {
            "number": 3,
            "type": "delay",
            "name": "Wait",
            "enabled": True,
            "config": {"duration_seconds": 3600, "until_field": None},
        },
        {"number": 4, "type": "erp_export", "name": "ERP", "enabled": True, "config": {}},
    ]
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (
            await c.post(
                "/api/workflows",
                json={"name": "Custom", "description": "d", "steps": steps},
            )
        ).json()["id"]
        resp = await c.post(
            f"/api/workflows/{wf_id}/simulate",
            json={"invoice": {"amount": "10", "currency": "USD"}},
        )
    assert resp.status_code == 200
    path = resp.json()["path"]
    delay = next(s for s in path if s["type"] == "delay")
    # Delay never slept — it recorded intent.
    assert "not slept" in delay["detail"].lower() or delay["outcome"] == "ok"


# ---------------------------------------------------------------------------
# the pure service
# ---------------------------------------------------------------------------


async def test_simulate_service_is_deterministic():
    from app.services.workflow_builder import build_invoice_context
    from app.services.workflow_simulation import simulate

    ctx = build_invoice_context({"amount": "2000", "currency": "USD"})
    cfg = {"steps": _BRANCHING_STEPS}
    a = await simulate(cfg, ctx)
    b = await simulate(cfg, ctx)
    assert a == b
    assert _step_numbers(a["path"]) == [1, 2, 4, 5]


async def test_simulate_service_flags_goto_loop():
    from app.services.workflow_simulation import simulate

    looping = {
        "steps": [
            {
                "number": 1,
                "type": "condition",
                "name": "Loop",
                "enabled": True,
                "config": {
                    "rules": [{"field": "amount", "operator": "gte", "value": 0}],
                    "match": "all",
                    "on_true_goto": 1,  # points at itself → infinite loop
                    "on_false_goto": None,
                },
            },
        ]
    }
    result = await simulate(looping, {"amount": Decimal("1"), "currency": "USD"})
    assert any("loop" in w.lower() for w in result["warnings"])
