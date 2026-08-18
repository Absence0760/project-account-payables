"""Out-of-office delegation — input validation, delegate eligibility, audit.

`POST /api/auth/delegation` decides who receives the caller's approval
assignments (`approval_chain.resolve_assignee` → `review.assign_reviewer`
rewrites `Invoice.assigned_to_id`). Three things were wrong with it:

* **Unhandled `ValueError` on caller-supplied input.** A non-UUID
  `delegate_to_id` and an unparseable `until` each escaped the handler as a bare
  `ValueError` — a 500 on ordinary bad input.
* **A naive `until` meant an ambiguous instant.** `datetime.fromisoformat`
  happily returns a naive datetime; asyncpg then stores it into the
  `timestamptz` column interpreted in the *session's* timezone, while
  `get_delegation` and `resolve_assignee` both compare against `now(UTC)`. The
  same submitted wall-clock time therefore meant a different expiry depending on
  server config. A past `until` was also accepted and simply never fired.
* **A deactivated delegate was accepted.** `assign_reviewer` reassigns the
  invoice to them unconditionally, so every routed approval landed on an account
  that can never sign in and the invoice sat owned by nobody.

Plus: changing who receives approval assignments is an access-control fact and
went unaudited, alone among the mutations in this module.

Driven directly against the handlers with a real control session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api.auth import SetDelegateRequest, clear_delegation, set_delegation
from app.models.user import User

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def delegation_users(realdb, monkeypatch):
    """A caller + a factory for candidate delegates in the same org.

    Control-plane rows persist across tests, so every row made here is removed
    on teardown. `dispatch_auth_audit` is captured rather than written — the
    tenant-DB write it performs is not what these tests are about.
    """
    audits: list[dict] = []

    async def _capture(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr("app.api.auth.dispatch_auth_audit", _capture)

    mk = realdb.control_sessionmaker()
    org_id = realdb.info("a").org_id
    created: list[uuid.UUID] = []

    async def _make(*, is_active: bool = True, org: uuid.UUID | None = None) -> User:
        user_id = uuid.uuid4()
        async with mk() as s:
            row = User(
                id=user_id,
                email=f"deleg-{user_id}@example.test",
                full_name="Delegation Probe",
                hashed_password=None,
                organization_id=org or org_id,
                is_active=is_active,
                must_change_password=False,
            )
            s.add(row)
            await s.commit()
        created.append(user_id)
        async with mk() as s:
            return (await s.execute(select(User).where(User.id == user_id))).scalar_one()

    yield mk, _make, audits

    async with mk() as s:
        await s.execute(delete(User).where(User.id.in_(created)))
        await s.commit()


def _future() -> str:
    return (datetime.now(UTC) + timedelta(days=3)).isoformat()


async def test_malformed_delegate_id_is_422_not_500(delegation_users):
    mk, make, _ = delegation_users
    caller = await make()
    async with mk() as s:
        with pytest.raises(HTTPException) as exc:
            await set_delegation(
                SetDelegateRequest(delegate_to_id="not-a-uuid", until=_future()), s, caller
            )
    assert exc.value.status_code == 422


async def test_malformed_until_is_422_not_500(delegation_users):
    mk, make, _ = delegation_users
    caller, delegate = await make(), await make()
    async with mk() as s:
        with pytest.raises(HTTPException) as exc:
            await set_delegation(
                SetDelegateRequest(delegate_to_id=str(delegate.id), until="tomorrow"), s, caller
            )
    assert exc.value.status_code == 422


async def test_past_until_is_refused(delegation_users):
    """A window that has already closed would leave the caller believing they
    are out of office while the delegation never fires."""
    mk, make, _ = delegation_users
    caller, delegate = await make(), await make()
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    async with mk() as s:
        with pytest.raises(HTTPException) as exc:
            await set_delegation(
                SetDelegateRequest(delegate_to_id=str(delegate.id), until=past), s, caller
            )
    assert exc.value.status_code == 422


async def test_deactivated_delegate_is_refused(delegation_users):
    mk, make, _ = delegation_users
    caller = await make()
    delegate = await make(is_active=False)
    async with mk() as s:
        with pytest.raises(HTTPException) as exc:
            await set_delegation(
                SetDelegateRequest(delegate_to_id=str(delegate.id), until=_future()), s, caller
            )
    assert exc.value.status_code == 404


async def test_cross_org_delegate_is_refused(delegation_users, realdb):
    mk, make, _ = delegation_users
    caller = await make()
    delegate = await make(org=realdb.info("b").org_id)
    async with mk() as s:
        with pytest.raises(HTTPException) as exc:
            await set_delegation(
                SetDelegateRequest(delegate_to_id=str(delegate.id), until=_future()), s, caller
            )
    assert exc.value.status_code == 404


async def test_naive_until_is_stored_as_utc_and_audited(delegation_users):
    """A naive ISO string must land as an explicit UTC instant, not one the DB
    session's timezone gets to reinterpret."""
    mk, make, audits = delegation_users
    caller, delegate = await make(), await make()
    naive = (datetime.now(UTC) + timedelta(days=2)).replace(tzinfo=None).isoformat()

    async with mk() as s:
        stored = await s.get(User, caller.id)
        resp = await set_delegation(
            SetDelegateRequest(delegate_to_id=str(delegate.id), until=naive), s, stored
        )
    assert resp.is_active is True

    async with mk() as s:
        row = await s.get(User, caller.id)
        assert row.delegate_to_id == delegate.id
        assert row.delegate_until is not None
        # Round-tripped through timestamptz: the instant must match the naive
        # wall-clock reading interpreted as UTC.
        assert row.delegate_until == datetime.fromisoformat(naive).replace(tzinfo=UTC)

    assert [a["action"] for a in audits] == ["auth.delegation.set"]
    assert audits[0]["details"]["delegate_to_id"] == str(delegate.id)


async def test_clearing_delegation_is_audited(delegation_users):
    mk, make, audits = delegation_users
    caller, delegate = await make(), await make()

    async with mk() as s:
        stored = await s.get(User, caller.id)
        await set_delegation(
            SetDelegateRequest(delegate_to_id=str(delegate.id), until=_future()), s, stored
        )
    async with mk() as s:
        stored = await s.get(User, caller.id)
        await clear_delegation(s, stored)

    async with mk() as s:
        row = await s.get(User, caller.id)
        assert row.delegate_to_id is None
        assert row.delegate_until is None

    assert [a["action"] for a in audits] == ["auth.delegation.set", "auth.delegation.cleared"]
