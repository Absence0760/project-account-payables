"""`PATCH /api/organization` type validation for well-known settings sub-keys.

The generic `settings` merge on this endpoint accepts an almost-arbitrary
client dict and merges it straight into `Organization.settings`, so a bad type
(a numeric `currency`, a non-numeric `cfo_approval_above`) used to persist
silently instead of being rejected at save. `_validate_settings_patch`
(app/api/organization.py) closes just these two specific type-confusion holes
— it is deliberately not a schema for the whole freeform settings bag.
"""

from __future__ import annotations

import pytest


async def _reset(realdb, key: str) -> None:
    async with realdb.client(key=key, role="admin") as c:
        await c.patch(
            "/api/organization",
            json={
                "settings": {
                    "invoice_defaults": {"currency": "USD"},
                    "payments": {"cfo_approval_above": None},
                }
            },
        )


@pytest.mark.asyncio
async def test_numeric_currency_rejected(realdb):
    """A numeric `invoice_defaults.currency` must 422, not persist."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.patch(
                "/api/organization",
                json={"settings": {"invoice_defaults": {"currency": 840}}},
            )
        assert resp.status_code == 422, resp.text
        assert "currency" in resp.json()["detail"].lower()

        async with realdb.client(key="a", role="admin") as c:
            get_resp = await c.get("/api/organization")
        assert get_resp.json()["settings"]["invoice_defaults"]["currency"] != 840
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_wrong_length_currency_rejected(realdb):
    """A 2-letter (or otherwise non-3-letter) currency code must 422."""
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.patch(
            "/api/organization",
            json={"settings": {"invoice_defaults": {"currency": "US"}}},
        )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_valid_currency_accepted(realdb):
    """A well-formed 3-letter currency code saves and round-trips."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.patch(
                "/api/organization",
                json={"settings": {"invoice_defaults": {"currency": "EUR"}}},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["settings"]["invoice_defaults"]["currency"] == "EUR"
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_non_numeric_cfo_threshold_rejected(realdb):
    """A string `payments.cfo_approval_above` must 422, not persist."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.patch(
                "/api/organization",
                json={"settings": {"payments": {"cfo_approval_above": "lots"}}},
            )
        assert resp.status_code == 422, resp.text
        assert "cfo_approval_above" in resp.json()["detail"].lower()

        async with realdb.client(key="a", role="admin") as c:
            get_resp = await c.get("/api/organization")
        payments_after = get_resp.json()["settings"].get("payments") or {}
        assert payments_after.get("cfo_approval_above") != "lots"
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_valid_numeric_cfo_threshold_accepted(realdb):
    """A well-typed numeric threshold saves and round-trips."""
    try:
        async with realdb.client(key="a", role="admin") as c:
            resp = await c.patch(
                "/api/organization",
                json={"settings": {"payments": {"cfo_approval_above": 5000}}},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["settings"]["payments"]["cfo_approval_above"] == 5000
    finally:
        await _reset(realdb, "a")


@pytest.mark.asyncio
async def test_null_cfo_threshold_accepted(realdb):
    """`null` clears the threshold — explicitly allowed, not a type error."""
    async with realdb.client(key="a", role="admin") as c:
        resp = await c.patch(
            "/api/organization",
            json={"settings": {"payments": {"cfo_approval_above": None}}},
        )
    assert resp.status_code == 200, resp.text
