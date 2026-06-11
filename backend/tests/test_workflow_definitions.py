"""Real-Postgres coverage for the workflow-definitions router.

Covers definition CRUD + activate/deactivate + delete guards:
- list auto-creates a Default Workflow when none exist (and is_default=True)
- create is gated to ROLE_ADMIN, starts inactive, and 422s on a missing name
- get / patch 404 on unknown id; get is auth-open, mutations are admin-only
- patch activation enforces the one-active-workflow invariant
- get_active_steps reflects the active definition's enabled flags
- delete guards: default → 409, active → 409, has instances → 409, ok → 204
- bulk-delete reports per-id deleted / failed reasons
- tenant isolation: a workflow under tenant A is 404 for tenant B

The one-active invariant and snapshot/instance delete-guard are the
load-bearing behaviours here, so they get direct DB assertions.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.workflow import WorkflowDefinition, WorkflowInstance

# A minimal valid steps payload for WorkflowDefinitionCreate.
_STEPS = [
    {
        "number": 1,
        "type": "extraction",
        "name": "Data Extraction",
        "enabled": True,
        "config": {"auto_approve_enabled": False, "auto_approve_threshold": 0.95},
    },
    {
        "number": 2,
        "type": "approval",
        "name": "Manager Approval",
        "enabled": True,
        "config": {"required": True, "approver_strategy": "manual"},
    },
]


async def _create_workflow(c, *, name="Custom WF", steps=None):
    return await c.post(
        "/api/workflows",
        json={"name": name, "description": "desc", "steps": steps or _STEPS},
    )


# ---------------------------------------------------------------------------
# list (auto-create default)
# ---------------------------------------------------------------------------


async def test_list_workflows_auto_creates_default(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["page"] == 1
    items = body["items"]
    assert len(items) == 1
    assert items[0]["name"] == "Default Workflow"
    assert items[0]["is_default"] is True
    assert items[0]["is_active"] is True

    # Persisted: a second list does not create a duplicate.
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        count = (await s.execute(select(func.count()).select_from(WorkflowDefinition))).scalar_one()
    assert count == 1


async def test_list_workflows_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/workflows")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


async def test_create_workflow_starts_inactive(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await _create_workflow(c)
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Custom WF"
    assert body["is_active"] is False
    assert body["is_default"] is False
    assert body["steps_config"]["steps"][0]["type"] == "extraction"


async def test_create_workflow_rbac(realdb):
    for role in ("ap_manager", "ap_clerk", "cfo"):
        async with realdb.client(key="a", role=role) as c:
            resp = await _create_workflow(c)
        assert resp.status_code == 403, role


async def test_create_workflow_missing_name_422(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/workflows", json={"description": "x", "steps": _STEPS})
    assert resp.status_code == 422


async def test_create_workflow_empty_name_422(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post("/api/workflows", json={"name": "", "steps": _STEPS})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


async def test_get_workflow_404(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.get(f"/api/workflows/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_get_workflow_happy_path(realdb):
    async with realdb.client(key="a", role="admin") as c:
        created = (await _create_workflow(c)).json()
        resp = await c.get(f"/api/workflows/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


async def test_get_workflow_tenant_isolation(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create_workflow(c)).json()["id"]
    # Same id queried under tenant B → 404 (its DB has no such row).
    async with realdb.client(key="b", role="admin") as c:
        resp = await c.get(f"/api/workflows/{wf_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# patch / activate-deactivate (one-active invariant)
# ---------------------------------------------------------------------------


async def test_patch_workflow_rbac(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create_workflow(c)).json()["id"]
    for role in ("ap_manager", "ap_clerk", "cfo"):
        async with realdb.client(key="a", role=role) as c:
            resp = await c.patch(f"/api/workflows/{wf_id}", json={"name": "Nope"})
        assert resp.status_code == 403, role


async def test_patch_workflow_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.patch(f"/api/workflows/{uuid.uuid4()}", json={"name": "x"})
    assert resp.status_code == 404


async def test_patch_workflow_updates_fields(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create_workflow(c)).json()["id"]
        resp = await c.patch(
            f"/api/workflows/{wf_id}",
            json={"name": "Renamed", "description": "new desc"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed"
    assert body["description"] == "new desc"


async def test_activating_workflow_deactivates_peers(realdb):
    org_id = realdb.info("a").org_id
    async with realdb.client(key="a", role="admin") as c:
        # The list call seeds the active default workflow.
        await c.get("/api/workflows")
        new_id = (await _create_workflow(c, name="Second")).json()["id"]
        # Activate the new one — the default must flip inactive.
        resp = await c.patch(f"/api/workflows/{new_id}", json={"is_active": True})
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        active = (
            (
                await s.execute(
                    select(WorkflowDefinition).where(
                        WorkflowDefinition.organization_id == org_id,
                        WorkflowDefinition.is_active.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )
    # Exactly one active definition org-wide.
    assert len(active) == 1
    assert str(active[0].id) == new_id


async def test_patch_steps_replaces_snapshotless_config(realdb):
    """Editing a definition's steps replaces its live steps_config in place
    (instances keep their own frozen snapshot — see delete-guard test)."""
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create_workflow(c)).json()["id"]
        new_steps = [{"number": 1, "type": "approval", "name": "Only Approval", "enabled": True}]
        resp = await c.patch(f"/api/workflows/{wf_id}", json={"steps": new_steps})
    assert resp.status_code == 200
    steps = resp.json()["steps_config"]["steps"]
    assert len(steps) == 1
    assert steps[0]["type"] == "approval"


# ---------------------------------------------------------------------------
# active steps
# ---------------------------------------------------------------------------


async def test_get_active_steps_reflects_active_definition(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        # Auto-creates the default (all steps disabled in DEFAULT_STEPS_CONFIG).
        resp = await c.get("/api/workflows/active/steps")
    assert resp.status_code == 200
    body = resp.json()
    # Default config disables every step type.
    assert body["extraction"] is False
    assert body["approval"] is False
    assert body["erp_export"] is False


# ---------------------------------------------------------------------------
# delete guards
# ---------------------------------------------------------------------------


async def test_delete_default_workflow_409(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await c.get("/api/workflows")).json()["items"][0]["id"]  # the default
        resp = await c.delete(f"/api/workflows/{wf_id}")
    assert resp.status_code == 409
    assert "default" in resp.json()["detail"].lower()


async def test_delete_active_workflow_409(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create_workflow(c)).json()["id"]
        await c.patch(f"/api/workflows/{wf_id}", json={"is_active": True})
        resp = await c.delete(f"/api/workflows/{wf_id}")
    assert resp.status_code == 409
    assert "active" in resp.json()["detail"].lower()


async def test_delete_inactive_workflow_204(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create_workflow(c)).json()["id"]
        resp = await c.delete(f"/api/workflows/{wf_id}")
    assert resp.status_code == 204

    mk = realdb.sessionmaker("a")
    async with mk() as s:
        gone = (
            await s.execute(
                select(WorkflowDefinition).where(WorkflowDefinition.id == uuid.UUID(wf_id))
            )
        ).scalar_one_or_none()
    assert gone is None


async def test_delete_workflow_with_instances_409(realdb):
    """A definition that is the snapshot source for a live instance can't
    be deleted — the FK + history would dangle."""
    org_id = realdb.info("a").org_id
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create_workflow(c)).json()["id"]

    # Attach an invoice + workflow instance bound to the definition.
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        inv = Invoice(
            invoice_number="INV-WF",
            vendor_name="V",
            amount=Decimal("10.00"),
            status=InvoiceStatus.new,
            organization_id=org_id,
        )
        s.add(inv)
        await s.flush()
        s.add(
            WorkflowInstance(
                definition_id=uuid.UUID(wf_id),
                invoice_id=inv.id,
                steps_config_snapshot={"steps": []},
            )
        )
        await s.commit()

    async with realdb.client(key="a", role="admin") as c:
        resp = await c.delete(f"/api/workflows/{wf_id}")
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["instance_count"] == 1


async def test_delete_workflow_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.delete(f"/api/workflows/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_delete_workflow_rbac(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create_workflow(c)).json()["id"]
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.delete(f"/api/workflows/{wf_id}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# bulk-delete
# ---------------------------------------------------------------------------


async def test_bulk_delete_reports_per_id(realdb):
    async with realdb.client(key="a", role="admin") as c:
        default_id = (await c.get("/api/workflows")).json()["items"][0]["id"]
        ok_id = (await _create_workflow(c, name="Deletable")).json()["id"]
        active_id = (await _create_workflow(c, name="Active")).json()["id"]
        await c.patch(f"/api/workflows/{active_id}", json={"is_active": True})
        bogus_id = str(uuid.uuid4())

        resp = await c.post(
            "/api/workflows/bulk-delete",
            json={
                "workflow_ids": [
                    ok_id,
                    default_id,
                    active_id,
                    bogus_id,
                    "not-a-uuid",
                ]
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == [ok_id]
    reasons = {f["workflow_id"]: f["reason"] for f in body["failed"]}
    assert reasons[default_id] == "default"
    assert reasons[active_id] == "active"
    assert reasons[bogus_id] == "not_found"
    assert reasons["not-a-uuid"] == "not_found"


async def test_bulk_delete_rbac(realdb):
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post("/api/workflows/bulk-delete", json={"workflow_ids": []})
    assert resp.status_code == 403
