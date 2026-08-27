"""`settings.mfa.required` vs the platform master switch `FEOH_MFA_ENABLED`.

Saving "Require two-factor authentication for all users" used to be a silent
no-op whenever `FEOH_MFA_ENABLED` is off (the shipped local-dev default) — no
signal anywhere that the toggle was inert. `PATCH`/`GET /api/organization` now
compute `settings.mfa.enforcement_active` fresh on every read (never
persisted) and the PATCH handler logs a warning when it accepts a
`required=true` save that can't currently take effect. See decisions.md §58.
"""

from __future__ import annotations

import pytest

from app.config import settings as app_settings


async def _reset_mfa(realdb, key: str) -> None:
    async with realdb.client(key=key, role="admin") as c:
        await c.patch("/api/organization", json={"settings": {"mfa": {"required": False}}})


@pytest.mark.asyncio
async def test_mfa_required_true_reports_enforcement_inactive_when_switch_off(realdb):
    """FEOH_MFA_ENABLED is off in the test env (the committed local-dev
    default) — saving required=true must be ACCEPTED (200), but the response
    must say enforcement is not actually active, not pretend it is."""
    assert app_settings.mfa_enabled is False  # sanity: the scenario this guards

    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.patch(
                "/api/organization", json={"settings": {"mfa": {"required": True}}}
            )
        assert resp.status_code == 200, resp.text
        mfa = resp.json()["settings"]["mfa"]
        assert mfa["required"] is True
        assert mfa["enforcement_active"] is False

        # GET agrees with what PATCH just returned — computed the same way.
        async with realdb.client(key="a", role="admin") as c:
            get_resp = await c.get("/api/organization")
        assert get_resp.status_code == 200, get_resp.text
        mfa_get = get_resp.json()["settings"]["mfa"]
        assert mfa_get["required"] is True
        assert mfa_get["enforcement_active"] is False
    finally:
        await _reset_mfa(realdb, "a")


@pytest.mark.asyncio
async def test_mfa_required_true_reports_enforcement_active_when_switch_on(realdb, monkeypatch):
    """With the platform switch on, the same save reports enforcement as
    actually active — the field tracks reality, not just the stored flag."""
    monkeypatch.setattr(app_settings, "mfa_enabled", True)
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.patch(
                "/api/organization", json={"settings": {"mfa": {"required": True}}}
            )
        assert resp.status_code == 200, resp.text
        mfa = resp.json()["settings"]["mfa"]
        assert mfa["required"] is True
        assert mfa["enforcement_active"] is True
    finally:
        await _reset_mfa(realdb, "a")


@pytest.mark.asyncio
async def test_mfa_required_false_reports_enforcement_inactive_regardless(realdb):
    """required=false ⇒ enforcement_active=false even if the platform switch
    is on — nothing is being enforced when the org opted out."""
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.patch("/api/organization", json={"settings": {"mfa": {"required": False}}})
    assert resp.status_code == 200, resp.text
    mfa = resp.json()["settings"]["mfa"]
    assert mfa["required"] is False
    assert mfa["enforcement_active"] is False
