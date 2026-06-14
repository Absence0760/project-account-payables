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
