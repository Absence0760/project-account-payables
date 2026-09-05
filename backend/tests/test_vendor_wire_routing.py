"""Separate WIRE vs ACH routing numbers on `Vendor.bank_details`.

Larger US banks publish a different ABA for incoming Fedwires than for ACH, so
one generic `routing_number` cannot express a payable wire at those banks. The
shape gains `wire_routing_number` alongside the original key — which keeps its
existing meaning (the ACH number), so no stored row is reinterpreted.

Three things have to hold and each is covered here:

1. **The new field travels the dual-control BEC gate like every other banking
   field.** A field that applied inline would be a one-person bank redirect.
2. **A malformed ABA fails loudly**, at staging AND at the apply chokepoint —
   the latter is what covers a payload staged through the supplier portal.
3. **The right number reaches the right rail** — wire rails prefer the wire ABA
   and fall back to the ACH one; ACH rails never borrow the wire one.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.vendor import Vendor
from app.models.vendor_change_request import VendorChangeRequest
from app.models.workflow import AuditLog
from app.services.payment_adapters.base import (
    PaymentPayload,
    resolve_routing_number,
)

TENANT = "a"

# Two real, checksum-valid ABAs (JPMorgan Chase NY ACH and Fedwire numbers are
# genuinely different at that bank — the exact case this models).
ACH_ABA = "021000021"
WIRE_ABA = "026009593"
BAD_ABA = "021000020"  # one digit off — fails the ABA checksum


async def _seed_vendor(mk, org_id, **kw) -> uuid.UUID:
    vendor_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Vendor(
                id=vendor_id,
                name=kw.get("name", "Wire Routing Test Co"),
                organization_id=org_id,
                status="active",
                source="manual",
                bank_details=kw.get("bank_details"),
            )
        )
        await s.commit()
    return vendor_id


async def _stage_portal_style(mk, org_id, vendor_id, bank_details) -> uuid.UUID:
    """A change request as the supplier portal writes one — no AP requester, so
    the approve path's proposer-segregation check doesn't bite. This is the
    payload shape whose validation the AP-side Pydantic model never sees."""
    req_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            VendorChangeRequest(
                id=req_id,
                vendor_id=vendor_id,
                organization_id=org_id,
                requested_by_vendor_user_id=uuid.uuid4(),
                change_type="bank_details",
                status="pending",
                proposed_value={"bank_details": bank_details},
            )
        )
        await s.commit()
    return req_id


# ---------------------------------------------------------------------------
# Rail selection (pure).
# ---------------------------------------------------------------------------


def test_wire_rail_reads_the_wire_routing_number():
    bank = {"routing_number": ACH_ABA, "wire_routing_number": WIRE_ABA}
    for method in ("wire", "international_wire"):
        sel = resolve_routing_number(bank, method)
        assert sel.number == WIRE_ABA, method
        assert sel.source == "wire"


def test_ach_family_rails_read_the_ach_routing_number():
    bank = {"routing_number": ACH_ABA, "wire_routing_number": WIRE_ABA}
    for method in ("ach", "international_ach", "rtp", "check"):
        sel = resolve_routing_number(bank, method)
        assert sel.number == ACH_ABA, method
        assert sel.source == "ach"


def test_wire_falls_back_to_the_ach_number_when_no_separate_wire_aba():
    """The common case: a bank publishing ONE ABA uses it for both. Refusing
    the wire here would break every vendor at a smaller institution."""
    sel = resolve_routing_number({"routing_number": ACH_ABA}, "wire")
    assert sel.number == ACH_ABA
    assert sel.source == "ach"


def test_ach_never_borrows_the_wire_number():
    """Deliberately asymmetric. A bank with two ABAs will not accept an ACH
    file addressed to its Fedwire number, so borrowing it converts a
    missing-data problem into a returned item at the vendor's expense."""
    sel = resolve_routing_number({"wire_routing_number": WIRE_ABA}, "ach")
    assert sel.number is None
    assert sel.source == "none"


def test_resolution_tolerates_a_missing_or_empty_bank_dict():
    for bank in (None, {}, {"routing_number": ""}, {"routing_number": "   "}):
        for method in ("ach", "wire"):
            sel = resolve_routing_number(bank, method)
            assert sel.number is None
            assert sel.source == "none"


def test_payload_routing_property_selects_per_its_own_method():
    """Adapters read `payload.routing`, never `vendor_bank["routing_number"]`
    — which is the ACH number and would misroute a wire."""
    from decimal import Decimal

    def payload(method: str) -> PaymentPayload:
        return PaymentPayload(
            correlation_id="c1",
            invoice_id="i1",
            invoice_number="INV-1",
            vendor_name="Wire Routing Test Co",
            amount=Decimal("100.00"),
            currency="USD",
            method=method,
            vendor_bank={"routing_number": ACH_ABA, "wire_routing_number": WIRE_ABA},
        )

    assert payload("wire").routing.number == WIRE_ABA
    assert payload("ach").routing.number == ACH_ABA


@pytest.mark.asyncio
async def test_mock_adapter_reports_the_routing_source_and_never_the_number():
    """Local dev + e2e run on the mock adapter, so the selection has to be
    observable there — as the PII-free label only."""
    from decimal import Decimal

    from app.services.payment_adapters.mock_adapter import MockPaymentAdapter

    adapter = MockPaymentAdapter({})
    result = await adapter.create_payment(
        PaymentPayload(
            correlation_id="c1",
            invoice_id="i1",
            invoice_number="INV-1",
            vendor_name="Wire Routing Test Co",
            amount=Decimal("100.00"),
            currency="USD",
            method="wire",
            vendor_bank={"routing_number": ACH_ABA, "wire_routing_number": WIRE_ABA},
        )
    )
    assert result.raw_response["routing_source"] == "wire"
    serialised = str(result.raw_response)
    assert WIRE_ABA not in serialised
    assert ACH_ABA not in serialised


# ---------------------------------------------------------------------------
# Validation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_malformed_wire_routing_number_is_refused_before_staging(realdb):
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={})

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/vendors/{vendor_id}/bank-change",
            json={
                "bank_details": {
                    "account_number": "12345678",
                    "routing_number": ACH_ABA,
                    "wire_routing_number": BAD_ABA,
                }
            },
        )
    assert resp.status_code == 422, resp.text
    # The FIELD may be named; nothing in the rejected payload may be echoed
    # back. A Pydantic field validator would have failed this: FastAPI renders
    # a ValidationError with the offending `input`, which is the whole
    # `bank_details` dict — account number and all — in the 422 body.
    assert BAD_ABA not in resp.text
    assert ACH_ABA not in resp.text
    assert "12345678" not in resp.text
    assert "wire_routing_number" in resp.text, "the caller still needs to know WHICH field"

    async with mk() as s:
        rows = (
            (
                await s.execute(
                    select(VendorChangeRequest).where(VendorChangeRequest.vendor_id == vendor_id)
                )
            )
            .scalars()
            .all()
        )
    assert rows == [], "a malformed wire ABA must never reach the staging queue"


@pytest.mark.asyncio
async def test_international_details_without_any_aba_still_stage(realdb):
    """A SEPA/SWIFT payee legitimately has no ABA at all — requiring one would
    refuse a whole class of real vendors."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={})

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(
            f"/api/vendors/{vendor_id}/bank-change",
            json={
                "bank_details": {
                    "iban": "DE89370400440532013000",
                    "swift_bic": "DEUTDEFF",
                    "country": "DE",
                }
            },
        )
    assert resp.status_code == 202, resp.text


@pytest.mark.asyncio
async def test_approve_refuses_a_malformed_wire_aba_staged_elsewhere(realdb):
    """The apply chokepoint re-validates, so a payload staged through a route
    with a looser schema (the supplier portal takes a free-form dict) can't be
    written onto the vendor row unvalidated."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={"bank_name": "Old Bank"})
    req_id = await _stage_portal_style(
        mk,
        info.org_id,
        vendor_id,
        {"account_number": "12345678", "wire_routing_number": BAD_ABA},
    )

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert resp.status_code == 422, resp.text
    assert BAD_ABA not in resp.text

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert "wire_routing_number" not in (v.bank_details or {})
        req = (
            await s.execute(select(VendorChangeRequest).where(VendorChangeRequest.id == req_id))
        ).scalar_one()
        assert req.status == "pending", "a refused apply must leave the request open"


# ---------------------------------------------------------------------------
# The dual-control staging path.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bank_change_stages_the_wire_aba_and_approval_applies_it(realdb):
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={"routing_number": ACH_ABA})

    async with realdb.client(key=TENANT, role="admin") as client:
        staged = await client.post(
            f"/api/vendors/{vendor_id}/bank-change",
            json={
                "bank_details": {
                    "account_number": "12345678",
                    "routing_number": ACH_ABA,
                    "wire_routing_number": WIRE_ABA,
                }
            },
        )
    assert staged.status_code == 202, staged.text
    req_id = staged.json()["id"]

    # NOT applied yet — that is the whole point of the gate.
    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert "wire_routing_number" not in (v.bank_details or {})

    # A SECOND user approves (the proposer is refused by segregation of duties).
    async with realdb.client(key=TENANT, role="ap_manager") as client:
        approved = await client.post(f"/api/vendors/change-requests/{req_id}/approve")
    assert approved.status_code == 200, approved.text

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert v.bank_details["wire_routing_number"] == WIRE_ABA
        assert v.bank_details["routing_number"] == ACH_ABA

    # And the rail selection now reads the freshly-approved wire number.
    assert resolve_routing_number(v.bank_details, "wire").number == WIRE_ABA


@pytest.mark.asyncio
async def test_patch_does_not_apply_a_wire_aba_inline(realdb):
    """`PATCH /vendors/{id}` stages bank details rather than applying them; the
    new field must not have opened a second, ungated door."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={"routing_number": ACH_ABA})

    async with realdb.client(key=TENANT, role="admin") as client:
        resp = await client.patch(
            f"/api/vendors/{vendor_id}",
            json={"bank_details": {"wire_routing_last4": "9593"}},
        )
    assert resp.status_code == 200, resp.text
    assert (resp.json()["bank_details"] or {}).get("wire_routing_last4") is None

    async with mk() as s:
        v = (await s.execute(select(Vendor).where(Vendor.id == vendor_id))).scalar_one()
        assert "wire_routing_last4" not in (v.bank_details or {})
        pending = (
            (
                await s.execute(
                    select(VendorChangeRequest).where(
                        VendorChangeRequest.vendor_id == vendor_id,
                        VendorChangeRequest.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(pending) == 1, "the PATCH must have STAGED the change, not dropped it"


@pytest.mark.asyncio
async def test_create_strips_and_stages_a_wire_aba(realdb):
    """The fake-new-payee BEC bypass: creating a vendor already carrying bank
    details is one person's action. The wire field is stripped like the rest."""
    async with realdb.client(key=TENANT, role="admin") as client:
        created = await client.post(
            "/api/vendors",
            json={
                "name": "Newly Created Wire Co",
                "code": f"NCW-{uuid.uuid4().hex[:6]}",
                "bank_details": {
                    "counterparty_id": "cp_1",
                    "wire_routing_last4": "9593",
                },
            },
        )
    assert created.status_code == 201, created.text
    assert created.json()["bank_details"] is None, "no bank details until approved"

    mk = realdb.sessionmaker(TENANT)
    vendor_id = uuid.UUID(created.json()["id"])
    async with mk() as s:
        pending = (
            (
                await s.execute(
                    select(VendorChangeRequest).where(
                        VendorChangeRequest.vendor_id == vendor_id,
                        VendorChangeRequest.status == "pending",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(pending) == 1
    assert pending[0].proposed_value["bank_details"]["wire_routing_last4"] == "9593"


# ---------------------------------------------------------------------------
# PII.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_wire_aba_never_lands_in_the_audit_trail(realdb):
    """`wire_routing_number` is banking data. It is a NEW secret-shaped key, so
    it has to be in `_BANK_SECRET_KEYS` — otherwise the audit summary's
    else-branch records it verbatim."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    vendor_id = await _seed_vendor(mk, info.org_id, bank_details={"routing_number": ACH_ABA})

    async with realdb.client(key=TENANT, role="admin") as client:
        staged = await client.post(
            f"/api/vendors/{vendor_id}/bank-change",
            json={"bank_details": {"wire_routing_number": WIRE_ABA}},
        )
        assert staged.status_code == 202, staged.text
        req_id = staged.json()["id"]
    async with realdb.client(key=TENANT, role="ap_manager") as client:
        assert (
            await client.post(f"/api/vendors/change-requests/{req_id}/approve")
        ).status_code == 200

    async with mk() as s:
        rows = (
            (await s.execute(select(AuditLog).where(AuditLog.entity_id == vendor_id)))
            .scalars()
            .all()
        )
    assert rows, "the change has to be audited at all"
    for row in rows:
        blob = str(row.details or {})
        assert WIRE_ABA not in blob, row.action
        # ...but the fact of the change, and a last-4, do have to be there.
    summaries = [(r.details or {}).get("proposed_change", {}).get("fields", {}) for r in rows]
    assert any(
        f.get("wire_routing_number", {}).get("new_last4") == WIRE_ABA[-4:] for f in summaries
    ), "the approver needs to see THAT the wire ABA changed, as a last-4"
