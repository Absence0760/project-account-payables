"""International-tax compute + rules + rate endpoints (realdb).

Exercises the HTTP surface end-to-end: auth gating, the org-config →
rate-adapter wiring, and the VAT/GST/withholding/rules/rate routes.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_rules_list_and_detail(realdb):
    try:
        client = realdb.client(key="a", role="ap_clerk")
        async with client:
            resp = await client.get("/api/international-tax/rules")
            assert resp.status_code == 200, resp.text
            codes = {r["country_code"] for r in resp.json()}
            assert {"GB", "DE", "AU", "IN", "CA"} <= codes

            detail = await client.get("/api/international-tax/rules/AU")
            assert detail.status_code == 200
            assert detail.json()["regime"] == "gst"
            assert detail.json()["registration_label"] == "ABN"

            missing = await client.get("/api/international-tax/rules/ZZ")
            assert missing.status_code == 404
    finally:
        await realdb.cleanup()


@pytest.mark.asyncio
async def test_rate_lookup_uses_mock_default(realdb):
    try:
        client = realdb.client(key="a", role="ap_manager")
        async with client:
            resp = await client.get("/api/international-tax/rate/GB")
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["provider"] == "mock"
            assert body["rate"] == "20"
            assert body["regime"] == "vat"
    finally:
        await realdb.cleanup()


@pytest.mark.asyncio
async def test_vat_endpoint_reverse_charge(realdb):
    try:
        client = realdb.client(key="a", role="ap_manager")
        async with client:
            resp = await client.post(
                "/api/international-tax/vat",
                json={
                    "net_amount": "1000",
                    "supplier_country": "DE",
                    "buyer_country": "FR",
                    "buyer_vat_registered": True,
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["reverse_charge"] is True
            assert body["vat_payable"] == 0.0
            # Reportable VAT is self-accounted at the BUYER's (FR) domestic
            # rate — 20% of 1000 = 200 — not the supplier's (DE) 19% = 190.
            # See GH #165.
            assert body["reportable_vat"] == 200.0
            assert body["vat_amount"] == 190.0
    finally:
        await realdb.cleanup()


@pytest.mark.asyncio
async def test_vat_endpoint_reverse_charge_uses_buyer_country_rate_issue_165(realdb):
    # Regression for GH #165: the endpoint resolves a SECOND rate for
    # buyer_country and reports at the buyer's rate under reverse charge,
    # not the supplier's. Mock adapter rates: DE 19%, FR 20%.
    try:
        client = realdb.client(key="a", role="ap_manager")
        async with client:
            resp = await client.post(
                "/api/international-tax/vat",
                json={
                    "net_amount": "1000",
                    "supplier_country": "DE",
                    "buyer_country": "FR",
                    "buyer_vat_registered": True,
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["reverse_charge"] is True
            assert body["vat_rate"] == "19"
            assert body["vat_amount"] == 190.0
            assert body["reportable_vat"] == 200.0
            assert body["reportable_vat"] != body["vat_amount"]
    finally:
        await realdb.cleanup()


@pytest.mark.asyncio
async def test_gst_endpoint_india_split(realdb):
    try:
        client = realdb.client(key="a", role="ap_clerk")
        async with client:
            resp = await client.post(
                "/api/international-tax/gst",
                json={"net_amount": "1000", "country": "IN"},
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["gst_amount"] == 180.0
            assert body["components"]["cgst"] == 90.0
            assert body["components"]["sgst"] == 90.0
    finally:
        await realdb.cleanup()


@pytest.mark.asyncio
async def test_withholding_endpoint(realdb):
    try:
        client = realdb.client(key="a", role="ap_manager")
        async with client:
            resp = await client.post(
                "/api/international-tax/withholding",
                json={
                    "gross_amount": "1000",
                    "supplier_country": "AU",
                    "category": "no_abn",
                },
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["withholding_amount"] == 470.0
            assert body["net_payable"] == 530.0
    finally:
        await realdb.cleanup()


@pytest.mark.asyncio
async def test_compute_endpoints_require_auth(realdb):
    try:
        client = realdb.client(key="a", role=None)
        async with client:
            resp = await client.post(
                "/api/international-tax/vat",
                json={"net_amount": "100", "supplier_country": "GB"},
            )
            assert resp.status_code == 401
    finally:
        await realdb.cleanup()
