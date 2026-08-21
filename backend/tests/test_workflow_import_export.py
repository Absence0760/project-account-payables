"""Coverage for workflow export / import.

- GET /export returns a portable WorkflowExport (schema_version=1).
- POST /import creates an inactive workflow from an export and round-trips.
- import validation rejects a malformed builder definition (422).
- import / export RBAC + tenant isolation.
"""

from __future__ import annotations

import uuid

_STEPS = [
    {"number": 1, "type": "extraction", "name": "Extract", "enabled": True, "config": {}},
    {
        "number": 2,
        "type": "condition",
        "name": "High?",
        "enabled": True,
        "config": {
            "rules": [{"field": "amount", "operator": "gte", "value": 1000}],
            "match": "all",
            "on_true_goto": 3,
            "on_false_goto": None,
        },
    },
    {"number": 3, "type": "erp_export", "name": "ERP", "enabled": True, "config": {}},
]


async def _create(c, *, name="Exportable", steps=None):
    return (
        await c.post(
            "/api/workflows",
            json={"name": name, "description": "the desc", "steps": steps or _STEPS},
        )
    ).json()


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


async def test_export_shape(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf = await _create(c)
        resp = await c.get(f"/api/workflows/{wf['id']}/export")
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == 1
    assert body["name"] == "Exportable"
    assert body["description"] == "the desc"
    assert body["steps_config"]["steps"][1]["type"] == "condition"


async def test_export_auth_open(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf = await _create(c)
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/workflows/{wf['id']}/export")
    assert resp.status_code == 200


async def test_export_unknown_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.get(f"/api/workflows/{uuid.uuid4()}/export")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# import — round-trip
# ---------------------------------------------------------------------------


async def test_import_round_trips_export(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf = await _create(c)
        export = (await c.get(f"/api/workflows/{wf['id']}/export")).json()
        resp = await c.post(
            "/api/workflows/import",
            json={"name": "Imported copy", "definition": export},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Imported copy"
    assert body["is_active"] is False
    assert body["is_default"] is False
    # Steps survived the round trip.
    assert body["steps_config"]["steps"] == export["steps_config"]["steps"]


async def test_import_falls_back_to_export_name(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf = await _create(c, name="Original Name")
        export = (await c.get(f"/api/workflows/{wf['id']}/export")).json()
        # No name in the request → use the export's name.
        resp = await c.post("/api/workflows/import", json={"definition": export})
    assert resp.status_code == 201
    assert resp.json()["name"] == "Original Name"


# ---------------------------------------------------------------------------
# import — validation rejects a bad definition
# ---------------------------------------------------------------------------


async def test_import_rejects_empty_steps(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/workflows/import",
            json={
                "name": "Empty",
                "definition": {"schema_version": 1, "name": "Empty", "steps_config": {"steps": []}},
            },
        )
    assert resp.status_code == 422


async def test_import_rejects_malformed_condition(realdb):
    bad_steps = [
        {
            "number": 1,
            "type": "condition",
            "name": "Broken",
            "enabled": True,
            # condition with an empty rules list → validate_builder_steps errors.
            "config": {"rules": [], "match": "all"},
        }
    ]
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/workflows/import",
            json={
                "name": "Bad",
                "definition": {
                    "schema_version": 1,
                    "name": "Bad",
                    "steps_config": {"steps": bad_steps},
                },
            },
        )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    # The validator's per-step error strings are surfaced.
    assert "errors" in detail and detail["errors"]


async def test_import_rejects_a_non_numeric_approval_money_threshold(realdb):
    """Import is the ONE save path that takes `steps_config` as a free-form dict
    — every other one types these fields `Decimal | None` through Pydantic. A
    non-numeric `max_invoice_amount` persisted here used to raise
    `InvalidOperation` out of `review._enforce_approval_thresholds`: a 500 on
    every approval under that workflow, with no path forward. The gates now fail
    closed, and the definition is refused at the boundary."""
    bad_steps = [
        {
            "number": 1,
            "type": "approval",
            "name": "Manager Approval",
            "enabled": True,
            "config": {
                "required": True,
                "max_invoice_amount": "ten thousand",
                "require_cfo_above": "5,000",
            },
        }
    ]
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/workflows/import",
            json={
                "name": "Bad thresholds",
                "definition": {
                    "schema_version": 1,
                    "name": "Bad thresholds",
                    "steps_config": {"steps": bad_steps},
                },
            },
        )
    assert resp.status_code == 422
    errors = resp.json()["detail"]["errors"]
    joined = " ".join(errors)
    assert "max_invoice_amount" in joined
    assert "require_cfo_above" in joined


async def test_import_accepts_wellformed_approval_thresholds(realdb):
    """Guard against over-rejecting: string-encoded numbers are how a JSONB
    export legitimately carries a Decimal."""
    good_steps = [
        {
            "number": 1,
            "type": "extraction",
            "name": "Extraction",
            "enabled": True,
            "config": {"auto_approve_enabled": True, "auto_approve_threshold": 0.95},
        },
        {
            "number": 2,
            "type": "approval",
            "name": "Manager Approval",
            "enabled": True,
            "config": {
                "required": True,
                "max_invoice_amount": "10000.00",
                "require_cfo_above": 5000,
                "auto_approve_below": None,
                "approval_chain": [{"name": "L1", "min_amount": 0, "max_amount": "9999.99"}],
            },
        },
    ]
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/workflows/import",
            json={
                "name": "Good thresholds",
                "definition": {
                    "schema_version": 1,
                    "name": "Good thresholds",
                    "steps_config": {"steps": good_steps},
                },
            },
        )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# RBAC + tenant isolation
# ---------------------------------------------------------------------------


async def test_import_rbac(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf = await _create(c)
        export = (await c.get(f"/api/workflows/{wf['id']}/export")).json()
    for role in ("ap_manager", "ap_clerk", "cfo"):
        async with realdb.client(key="a", role=role) as c:
            resp = await c.post("/api/workflows/import", json={"definition": export})
        assert resp.status_code == 403, role


async def test_export_tenant_isolation(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf = await _create(c)
    async with realdb.client(key="b", role="admin") as c:
        resp = await c.get(f"/api/workflows/{wf['id']}/export")
    assert resp.status_code == 404
