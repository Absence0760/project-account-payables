"""Coverage for workflow versioning: snapshot-on-update, manual snapshot,
restore, and diff.

- PATCH that changes steps auto-snapshots the PRIOR steps_config first.
- POST /versions manually snapshots the current steps_config.
- POST /restore/{version_id} snapshots current, then applies the chosen version.
- GET /versions/diff compares two versions (or a version vs. current).
- mutation routes are ROLE_ADMIN; read routes are auth-open; tenant isolation.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.workflow import WorkflowVersion

_STEPS_V1 = [
    {"number": 1, "type": "extraction", "name": "Extract", "enabled": True, "config": {}},
    {"number": 2, "type": "approval", "name": "Approve", "enabled": True, "config": {}},
]
_STEPS_V2 = [
    {"number": 1, "type": "extraction", "name": "Extract", "enabled": True, "config": {}},
    {"number": 2, "type": "approval", "name": "Approve (renamed)", "enabled": True, "config": {}},
    {"number": 3, "type": "erp_export", "name": "ERP", "enabled": True, "config": {}},
]


async def _create(c, *, name="Versioned WF", steps=None):
    return await c.post(
        "/api/workflows",
        json={"name": name, "description": "d", "steps": steps or _STEPS_V1},
    )


# ---------------------------------------------------------------------------
# auto-snapshot on PATCH that changes steps
# ---------------------------------------------------------------------------


async def test_patch_steps_auto_snapshots_prior(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        # First step-change PATCH snapshots the prior (V1) config.
        await c.patch(f"/api/workflows/{wf_id}", json={"steps": _STEPS_V2})
        versions = (await c.get(f"/api/workflows/{wf_id}/versions")).json()["items"]
    assert len(versions) == 1
    # The snapshot holds the PRIOR config (V1), not the new one.
    snap_steps = versions[0]["steps_config"]["steps"]
    assert len(snap_steps) == 2
    assert snap_steps[1]["name"] == "Approve"


async def test_patch_name_only_does_not_snapshot(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        await c.patch(f"/api/workflows/{wf_id}", json={"name": "Renamed only"})
        versions = (await c.get(f"/api/workflows/{wf_id}/versions")).json()["items"]
    assert versions == []


async def test_patch_identical_steps_does_not_snapshot(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        # PATCH with the same steps → no-op change → no version.
        await c.patch(f"/api/workflows/{wf_id}", json={"steps": _STEPS_V1})
        versions = (await c.get(f"/api/workflows/{wf_id}/versions")).json()["items"]
    assert versions == []


# ---------------------------------------------------------------------------
# manual snapshot
# ---------------------------------------------------------------------------


async def test_create_version_manual(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        resp = await c.post(f"/api/workflows/{wf_id}/versions", json={"note": "before launch"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["version_number"] == 1
    assert body["note"] == "before launch"
    assert body["steps_config"]["steps"][0]["type"] == "extraction"


async def test_version_numbers_increment(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        v1 = (await c.post(f"/api/workflows/{wf_id}/versions", json={"note": None})).json()
        v2 = (await c.post(f"/api/workflows/{wf_id}/versions", json={"note": None})).json()
    assert v1["version_number"] == 1
    assert v2["version_number"] == 2


async def test_create_version_rbac(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
    async with realdb.client(key="a", role="ap_manager") as c:
        resp = await c.post(f"/api/workflows/{wf_id}/versions", json={"note": "x"})
    assert resp.status_code == 403


async def test_list_versions_auth_open(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        await c.post(f"/api/workflows/{wf_id}/versions", json={"note": None})
    # ap_clerk (read) can list.
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get(f"/api/workflows/{wf_id}/versions")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


async def test_restore_applies_chosen_version(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        # Edit to V2 (auto-snapshots V1 as version 1).
        await c.patch(f"/api/workflows/{wf_id}", json={"steps": _STEPS_V2})
        v1_id = (await c.get(f"/api/workflows/{wf_id}/versions")).json()["items"][0]["id"]
        # Restore V1.
        resp = await c.post(f"/api/workflows/{wf_id}/restore/{v1_id}")
    assert resp.status_code == 200
    restored_steps = resp.json()["steps_config"]["steps"]
    assert len(restored_steps) == 2  # back to V1's 2 steps
    assert restored_steps[1]["name"] == "Approve"

    # Restore itself snapshotted the pre-restore (V2) state → now 2 versions.
    mk = realdb.sessionmaker("a")
    async with mk() as s:
        count = (
            await s.execute(
                select(func.count())
                .select_from(WorkflowVersion)
                .where(WorkflowVersion.definition_id == uuid.UUID(wf_id))
            )
        ).scalar_one()
    assert count == 2


async def test_restore_unknown_version_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        resp = await c.post(f"/api/workflows/{wf_id}/restore/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_restore_rbac(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        v_id = (await c.post(f"/api/workflows/{wf_id}/versions", json={"note": None})).json()["id"]
    async with realdb.client(key="a", role="cfo") as c:
        resp = await c.post(f"/api/workflows/{wf_id}/restore/{v_id}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


async def test_diff_version_vs_current(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        # Snapshot V1 manually, then edit to V2.
        v1_id = (await c.post(f"/api/workflows/{wf_id}/versions", json={"note": None})).json()["id"]
        await c.patch(f"/api/workflows/{wf_id}", json={"steps": _STEPS_V2})
        resp = await c.get(f"/api/workflows/{wf_id}/versions/diff?from={v1_id}&to=current")
    assert resp.status_code == 200
    diff = resp.json()
    assert diff["to_version"] == "current"
    kinds = {(ch["kind"], ch["step_number"]) for ch in diff["changes"]}
    # Step 3 was added; step 2's name changed.
    assert ("added", 3) in kinds
    assert any(ch["kind"] == "changed" and ch["step_number"] == 2 for ch in diff["changes"])


async def test_diff_unknown_version_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        resp = await c.get(f"/api/workflows/{wf_id}/versions/diff?from={uuid.uuid4()}&to=current")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# tenant isolation
# ---------------------------------------------------------------------------


async def test_versions_tenant_isolation(realdb):
    async with realdb.client(key="a", role="admin") as c:
        wf_id = (await _create(c)).json()["id"]
        await c.post(f"/api/workflows/{wf_id}/versions", json={"note": None})
    # Tenant B can't see tenant A's workflow at all.
    async with realdb.client(key="b", role="admin") as c:
        resp = await c.get(f"/api/workflows/{wf_id}/versions")
    assert resp.status_code == 404
