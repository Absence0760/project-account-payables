"""`POST /api/invoices/{id}/assign` resolves the reviewer inside the caller's org.

`users` is a CONTROL-PLANE table, so the reviewer lookup is the one place on
this route where a bare ``WHERE id = :id`` reaches every tenant's accounts. It
was bare: an admin / ap_manager of tenant A could stamp tenant B's user onto
``Invoice.assigned_to_id``, and ``review.assign_reviewer`` then dispatched the
``invoice_assigned`` notification to them — an email carrying tenant A's invoice
number, vendor name and amount, delivered to somebody in another tenant. The
invoice was left owned by an account that can never act on it, so it also sat in
the queue owned by nobody.

Two sibling routes already got this right and are the shape copied here:
``POST /api/exceptions/{id}/assign`` ("the user must belong to the same
organization") and ``POST /api/auth/delegation`` (same-org **and** active — the
deactivated-delegate guard exists for exactly this quiet-failure reason).
``GET /api/invoices/assignable-reviewers``, this endpoint's own picker, only
ever offers active same-org users, so the API now accepts precisely what the UI
offers.

Requires the dev Postgres (`pnpm db:up`).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.invoice import Invoice, InvoiceStatus
from app.models.user import User

pytestmark = pytest.mark.asyncio

TENANT = "a"


async def _seed_invoice(mk, org_id) -> uuid.UUID:
    """One `ready_for_review` invoice — the only status `/assign` accepts."""
    inv_id = uuid.uuid4()
    async with mk() as s:
        s.add(
            Invoice(
                id=inv_id,
                organization_id=org_id,
                invoice_number=f"ASSIGN-{uuid.uuid4().hex[:8]}",
                vendor_name="Scope Test Vendor",
                amount=Decimal("100.00"),
                currency="USD",
                status=InvoiceStatus.ready_for_review,
            )
        )
        await s.commit()
    return inv_id


async def _add_user(realdb, *, org_id, full_name: str, is_active: bool) -> uuid.UUID:
    from app.utils.passwords import pwd_context

    uid = uuid.uuid4()
    mk = realdb.control_sessionmaker()
    async with mk() as s:
        s.add(
            User(
                id=uid,
                email=f"{uuid.uuid4().hex[:10]}@assign-scope.test",
                full_name=full_name,
                hashed_password=pwd_context.hash("Passw0rd!xyz"),
                is_active=is_active,
                organization_id=org_id,
                must_change_password=False,
            )
        )
        await s.commit()
    return uid


async def _assigned_to(mk, inv_id):
    async with mk() as s:
        inv = (await s.execute(select(Invoice).where(Invoice.id == inv_id))).scalar_one()
        return inv.assigned_to_id, inv.assigned_to


async def test_cannot_assign_to_another_tenants_user(realdb):
    """The cross-tenant hole: tenant B's user must not become tenant A's reviewer."""
    info_a = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_invoice(mk, info_a.org_id)
    foreign_user_id = realdb.info("b").users["ap_manager"]

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(
            f"/api/invoices/{inv_id}/assign", json={"user_id": str(foreign_user_id)}
        )

    assert resp.status_code == 404, resp.text
    assigned_id, assigned_name = await _assigned_to(mk, inv_id)
    assert assigned_id is None, "a foreign tenant's user was stamped onto the invoice"
    assert assigned_name is None


async def test_cannot_assign_to_a_deactivated_user(realdb):
    """Same quiet failure `POST /api/auth/delegation` already refuses: the
    invoice would be owned by an account that can never sign in."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_invoice(mk, info.org_id)
    retired = await _add_user(
        realdb, org_id=info.org_id, full_name="Retired Reviewer", is_active=False
    )

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/assign", json={"user_id": str(retired)})

    assert resp.status_code == 404, resp.text
    assigned_id, _ = await _assigned_to(mk, inv_id)
    assert assigned_id is None


async def test_an_active_same_org_reviewer_still_assigns(realdb):
    """Surgical: the guard narrows the lookup, it doesn't break the happy path."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_invoice(mk, info.org_id)
    reviewer_id = info.users["ap_manager"]

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/assign", json={"user_id": str(reviewer_id)})

    assert resp.status_code == 200, resp.text
    assigned_id, assigned_name = await _assigned_to(mk, inv_id)
    assert assigned_id == reviewer_id
    assert assigned_name


async def test_a_malformed_user_id_is_a_validation_error(realdb):
    """`AssignReviewerRequest.user_id` is a bare `str`, so the UUID parse used to
    raise out of the handler — a 500 on attacker-controlled input."""
    info = realdb.info(TENANT)
    mk = realdb.sessionmaker(TENANT)
    inv_id = await _seed_invoice(mk, info.org_id)

    async with realdb.client(key=TENANT, role="admin") as c:
        resp = await c.post(f"/api/invoices/{inv_id}/assign", json={"user_id": "not-a-uuid"})

    assert resp.status_code == 422, resp.text


async def test_notification_recipients_are_org_scoped(realdb):
    """Defense in depth at the dispatch layer.

    `notify_event`'s recipient loop documents "Recipient no longer exists /
    wrong org — skip silently", but `_load_recipients` never filtered by org, so
    the wrong-org half of that comment was not true. Any caller that fails to
    scope its own lookup must not be able to address another tenant's inbox.
    """
    from app.database import dispatch_engine_scope
    from app.services.notification_dispatch import _load_recipients

    info_a = realdb.info(TENANT)
    own = info_a.users["ap_manager"]
    foreign = realdb.info("b").users["ap_manager"]

    ctrl_mk = realdb.control_sessionmaker()
    async with dispatch_engine_scope(control_sessionmaker=ctrl_mk):
        scoped = await _load_recipients([own, foreign], info_a.org_id)
        unscoped = await _load_recipients([own, foreign])

    assert set(scoped) == {own}, "a foreign-org recipient resolved for this tenant's dispatch"
    # The unscoped call is what every non-`notify_event` caller would get; both
    # ids exist, so the scoped result above is a real filter, not an empty DB.
    assert set(unscoped) == {own, foreign}
