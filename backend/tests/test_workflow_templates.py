"""Coverage for the no-code workflow template library + from-template create.

- GET /api/workflows/templates lists the pre-built templates (auth-open).
- POST /api/workflows/from-template clones a template into a fresh, inactive
  definition (ROLE_ADMIN-gated); unknown key → 404; bad role → 403.
"""

from __future__ import annotations

from app.services.workflow_templates import get_template, list_templates

# ---------------------------------------------------------------------------
# the static library itself
# ---------------------------------------------------------------------------


def test_template_library_shape():
    templates = list_templates()
    assert templates, "expected at least one template"
    keys = {t["key"] for t in templates}
    # The five the spec calls out.
    assert {
        "simple_approval",
        "high_value_cfo_routing",
        "parallel_approvers",
        "auto_approve_small",
        "webhook_email_notify",
    } <= keys
    for t in templates:
        assert set(t) >= {"key", "name", "description", "category", "steps_config"}
        steps = t["steps_config"]["steps"]
        assert steps and all("number" in s and "type" in s for s in steps)


def test_get_template_unknown_returns_none():
    assert get_template("does-not-exist") is None


def test_high_value_template_uses_condition_step():
    t = get_template("high_value_cfo_routing")
    types = [s["type"] for s in t["steps_config"]["steps"]]
    assert "condition" in types


# ---------------------------------------------------------------------------
# GET /templates
# ---------------------------------------------------------------------------


async def test_list_templates_endpoint(realdb):
    async with realdb.client(key="a", role="ap_clerk") as c:
        resp = await c.get("/api/workflows/templates")
    assert resp.status_code == 200
    items = resp.json()["items"]
    keys = {t["key"] for t in items}
    assert "simple_approval" in keys


async def test_list_templates_requires_auth(realdb):
    async with realdb.client(key="a", role=None) as c:
        resp = await c.get("/api/workflows/templates")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /from-template
# ---------------------------------------------------------------------------


async def test_from_template_creates_inactive_workflow(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/workflows/from-template",
            json={"template_key": "high_value_cfo_routing", "name": "My Routing WF"},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Routing WF"
    assert body["is_active"] is False
    assert body["is_default"] is False
    # Cloned the template's steps verbatim.
    expected = get_template("high_value_cfo_routing")["steps_config"]["steps"]
    assert len(body["steps_config"]["steps"]) == len(expected)


async def test_from_template_unknown_key_404(realdb):
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.post(
            "/api/workflows/from-template",
            json={"template_key": "nope", "name": "X"},
        )
    assert resp.status_code == 404


async def test_from_template_rbac(realdb):
    for role in ("ap_manager", "ap_clerk", "cfo"):
        async with realdb.client(key="a", role=role) as c:
            resp = await c.post(
                "/api/workflows/from-template",
                json={"template_key": "simple_approval", "name": "X"},
            )
        assert resp.status_code == 403, role
