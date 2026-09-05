"""`/auth/login` must not leak account existence through its audit write.

The login handler already equalizes the *password* cost: an unknown address
runs `dummy_verify()` so the bcrypt burn matches a real verification. But it
used to ADDITIONALLY `await dispatch_auth_audit(...)` whenever the address had
an organization — a control-plane lookup plus a tenant-DB session, connect and
commit. An address with no account skipped that entirely, so the two rejections
differed by a whole database round trip: the same enumeration oracle
`dummy_verify` exists to close, reintroduced one line below it.

Neither obvious "fix" is allowed (guard rail 4): dropping the row is worse than
the leak, and padding the unknown-address path with a matching sleep or a
throwaway query is masking. The fix is to take the write OFF the response path
entirely — `services.audit_dispatch.queue_auth_audit` spawns it as a task — so
*neither* branch pays for it, and then to route both rejections through one
shared tail (`api.auth._reject_login`) so they are identical by construction.

What these tests pin, in order:

1. Both rejections perform the SAME sequence of awaited operations. Asserted on
   a recorded call sequence, never on wall-clock timing — the timing is the
   symptom, the structural difference is the bug, and a clock assertion would
   be flaky besides.
2. The `auth.login.failure` row is still written for a known address.
3. A failure of the queued write neither breaks the response nor disappears
   silently — it surfaces at ERROR, PII-free.
4. The unknown-address path still writes no row (there is no tenant DB to route
   one to) and still pays the `dummy_verify` cost.

All four fail against the pre-fix handler: 1 records an extra awaited
`dispatch_auth_audit` on the known path only, 2–4 exercise machinery
(`queue_auth_audit` / `drain_auth_audits`) the pre-fix code does not have.

No Postgres and no Redis: the DB is an `AsyncMock` and the throttle helpers are
patched, exactly as `tests/test_auth_events.py` does it.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

KNOWN_EMAIL = "ada@acme.com"
UNKNOWN_EMAIL = "ghost@nowhere.example"
SUBMITTED_PASSWORD = "not-the-right-one"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _fake_request(ip: str = "10.0.0.1"):
    req = MagicMock()
    req.client = SimpleNamespace(host=ip)
    req.headers = {"user-agent": "pytest"}
    return req


def _db_returning(user):
    db = AsyncMock()
    user_result = MagicMock()
    user_result.scalar_one_or_none = MagicMock(return_value=user)
    db.execute = AsyncMock(return_value=user_result)
    return db


def _known_user(*, is_active: bool = True, hashed_password: str = "$2b$12$stub"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=KNOWN_EMAIL,
        hashed_password=hashed_password,
        organization_id=uuid.uuid4(),
        is_active=is_active,
        must_change_password=False,
        mfa_enabled=False,
        mfa_secret=None,
        full_name="Ada",
    )


class _Recorder:
    """Ordered log of everything the handler *awaits* on the response path.

    `dummy_verify` and `verify_password` are recorded under one label: they are
    the same bcrypt burn, deliberately so, and the invariant is that each
    rejection pays exactly one of them. Anything that is not awaited (the
    `queue_auth_audit` call) is kept in a separate list, because a call that
    spawns a task and returns costs the response path nothing.
    """

    def __init__(self) -> None:
        self.awaited: list[str] = []
        self.queued: list[dict] = []

    def _record(self, label: str):
        async def _fn(*args, **kwargs):
            self.awaited.append(label)
            if label == "verify_password":
                return False  # wrong password

        return _fn

    def queue(self, **kwargs) -> None:
        self.queued.append(kwargs)


def _patched_login(recorder: _Recorder, *, queue=None):
    """Patch every awaited collaborator of `login` onto `recorder`."""
    from app.api import auth as auth_mod

    return (
        patch.object(auth_mod, "check_rate_limit", recorder._record("check_rate_limit")),
        patch.object(auth_mod, "check_auth_failures", recorder._record("check_auth_failures")),
        patch.object(auth_mod, "record_auth_failure", recorder._record("record_auth_failure")),
        patch.object(auth_mod, "dummy_verify", recorder._record("password_hash_cost")),
        patch.object(auth_mod, "verify_password", recorder._record("password_hash_cost")),
        patch.object(auth_mod, "queue_auth_audit", queue or recorder.queue),
    )


async def _attempt_login(recorder: _Recorder, *, user, email: str, queue=None) -> HTTPException:
    from app.api import auth as auth_mod
    from app.schemas.auth import LoginRequest

    db = _db_returning(user)
    patches = _patched_login(recorder, queue=queue)
    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException) as exc:
            await auth_mod.login(
                LoginRequest(email=email, password=SUBMITTED_PASSWORD),
                _fake_request(),
                db,
            )
    finally:
        for p in reversed(patches):
            p.stop()
    # `db.execute` is the account lookup — it happens on both paths, and it is
    # the only await the recorder cannot intercept, so record it by position.
    assert db.execute.await_count == 1
    return exc.value


# ---------------------------------------------------------------------------
# 1. The two rejections are structurally identical
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_and_unknown_rejections_await_the_same_operations():
    """The deliverable. An unknown address and a known one with the wrong
    password must reach the 401 through the same awaits, in the same order.

    Pre-fix this fails: the known path appends an extra awaited
    `dispatch_auth_audit` (a control-plane query + a tenant-DB connect and
    commit) that the unknown path never performs.
    """
    known = _Recorder()
    known_exc = await _attempt_login(known, user=_known_user(), email=KNOWN_EMAIL)

    unknown = _Recorder()
    unknown_exc = await _attempt_login(unknown, user=None, email=UNKNOWN_EMAIL)

    assert known.awaited == unknown.awaited
    assert known.awaited == [
        "check_rate_limit",
        "check_auth_failures",
        "password_hash_cost",
        "record_auth_failure",
    ]
    # Same response, too — an opaque 401 either way.
    assert known_exc.status_code == unknown_exc.status_code == 401
    assert known_exc.detail == unknown_exc.detail == "Invalid credentials"

    # The audit is attempted identically from both branches — one non-awaited
    # call each. Only the organization differs, and that is what decides
    # whether a row can be routed anywhere at all.
    assert len(known.queued) == len(unknown.queued) == 1
    assert known.queued[0]["organization_id"] is not None
    assert unknown.queued[0]["organization_id"] is None
    assert known.queued[0]["action"] == unknown.queued[0]["action"] == "auth.login.failure"


@pytest.mark.asyncio
async def test_deactivated_account_rejection_is_identical_too():
    """A deactivated (or password-less) account is the *other* known-address
    rejection, and it used to take the same extra round trip. It funnels
    through the same tail, so it must record the same awaits."""
    deactivated = _Recorder()
    await _attempt_login(deactivated, user=_known_user(is_active=False), email=KNOWN_EMAIL)

    unknown = _Recorder()
    await _attempt_login(unknown, user=None, email=UNKNOWN_EMAIL)

    assert deactivated.awaited == unknown.awaited
    assert deactivated.queued[0]["details"]["reason"] == "inactive"


# ---------------------------------------------------------------------------
# 2. The row is still written
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_known_address_failure_still_writes_the_audit_row():
    """Moving the write off the response path must not lose it. Here
    `queue_auth_audit` is NOT patched — the real one runs, and the row lands
    once the spawned task is drained."""
    from app.services import audit_dispatch

    written: list[dict] = []

    async def _write(**kwargs):
        written.append(kwargs)

    user = _known_user()
    recorder = _Recorder()
    with patch.object(audit_dispatch, "_write_auth_audit", _write):
        await _attempt_login(
            recorder, user=user, email=KNOWN_EMAIL, queue=audit_dispatch.queue_auth_audit
        )
        # The response has already been decided; the write is still in flight.
        await audit_dispatch.drain_auth_audits()

    assert len(written) == 1
    row = written[0]
    assert row["action"] == "auth.login.failure"
    assert row["organization_id"] == user.organization_id
    assert row["actor_id"] is None
    assert row["details"]["reason"] == "bad_password"
    assert row["details"]["email"] == KNOWN_EMAIL
    assert row["details"]["ip"] == "10.0.0.1"


# ---------------------------------------------------------------------------
# 3. A failed queued write is survivable AND visible, PII-free
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_queued_audit_failure_does_not_break_login_and_is_logged_pii_free(caplog):
    """Off-the-response-path must not become best-effort-in-practice.

    The write is deliberately made to fail with an exception whose message
    embeds the submitted address — which is what a driver error echoing its
    parameters looks like. The login must still return its 401, the loss must
    be reported at ERROR (it is SOX evidence and nothing retries it), and the
    address must not reach the log.
    """
    from app.services import audit_dispatch

    class _DriverBlewUp(RuntimeError):
        pass

    async def _write(**kwargs):
        raise _DriverBlewUp(f"could not insert row for {KNOWN_EMAIL} / {SUBMITTED_PASSWORD}")

    recorder = _Recorder()
    with caplog.at_level(logging.INFO, logger="app.services.audit_dispatch"):
        with patch.object(audit_dispatch, "_write_auth_audit", _write):
            exc = await _attempt_login(
                recorder,
                user=_known_user(),
                email=KNOWN_EMAIL,
                queue=audit_dispatch.queue_auth_audit,
            )
            await audit_dispatch.drain_auth_audits()

    # The caller was never affected.
    assert exc.status_code == 401
    assert exc.detail == "Invalid credentials"

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a lost auth audit row must be surfaced, not swallowed"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "_DriverBlewUp" in joined  # the class, so the loss is diagnosable
    assert "auth.login.failure" in joined
    # ...and nothing that identifies the account or the credential.
    assert KNOWN_EMAIL not in joined
    assert SUBMITTED_PASSWORD not in joined
    assert "could not insert row" not in joined


# ---------------------------------------------------------------------------
# 4. The unknown address writes nothing — and still pays the bcrypt cost
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_address_writes_no_row_but_still_pays_dummy_verify():
    """There is no organization, so there is no tenant DB to route a row to —
    the drop is real and stays. What must NOT be dropped is the equalizing
    hash: `dummy_verify` is the reason the 401 timings match at all."""
    from app.api import auth as auth_mod
    from app.services import audit_dispatch

    written: list[dict] = []

    async def _write(**kwargs):
        written.append(kwargs)

    recorder = _Recorder()
    with patch.object(audit_dispatch, "_write_auth_audit", _write):
        await _attempt_login(
            recorder, user=None, email=UNKNOWN_EMAIL, queue=audit_dispatch.queue_auth_audit
        )
        await audit_dispatch.drain_auth_audits()

    assert written == []
    assert recorder.awaited.count("password_hash_cost") == 1

    # And the equalizing hash is really `dummy_verify` — not `verify_password`
    # against a stub, which would be a different (cheaper) code path.
    unpatched = _Recorder()
    dummy_calls: list[int] = []

    async def _dummy():
        dummy_calls.append(1)

    patches = (
        patch.object(auth_mod, "check_rate_limit", unpatched._record("check_rate_limit")),
        patch.object(auth_mod, "check_auth_failures", unpatched._record("check_auth_failures")),
        patch.object(auth_mod, "record_auth_failure", unpatched._record("record_auth_failure")),
        patch.object(auth_mod, "dummy_verify", _dummy),
        patch.object(auth_mod, "queue_auth_audit", unpatched.queue),
    )
    from app.schemas.auth import LoginRequest

    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException):
            await auth_mod.login(
                LoginRequest(email=UNKNOWN_EMAIL, password=SUBMITTED_PASSWORD),
                _fake_request(),
                _db_returning(None),
            )
    finally:
        for p in reversed(patches):
            p.stop()

    assert dummy_calls == [1]


# ---------------------------------------------------------------------------
# 5. The supplier-portal twin has the SAME asymmetry, and the same fix
# ---------------------------------------------------------------------------
#
# The portal is not a copy-paste of the employee path: its failure budget is
# tenant-scoped (`slug + email`, because a vendor address is unique only within
# a tenant DB) and it already holds a tenant session from `get_tenant_db`. None
# of that changes the shape of the leak — `dispatch_auth_audit` still resolves
# the tenant DB from the control plane and opens its OWN session to commit
# through, so a known vendor address paid for a round trip an unknown one
# skipped, exactly like the employee twin.


def _vendor_user(*, is_active: bool = True, hashed_password: str = "$2b$12$stub", org=True):
    return SimpleNamespace(
        id=uuid.uuid4(),
        email=KNOWN_EMAIL,
        hashed_password=hashed_password,
        organization_id=uuid.uuid4() if org else None,
        vendor_id=uuid.uuid4(),
        is_active=is_active,
        must_change_password=False,
        mfa_enabled=False,
        mfa_secret=None,
    )


def _patched_portal_login(recorder: _Recorder, *, queue=None):
    from app.api import portal_auth as portal_mod

    return (
        patch.object(portal_mod, "check_rate_limit", recorder._record("check_rate_limit")),
        patch.object(portal_mod, "check_auth_failures", recorder._record("check_auth_failures")),
        patch.object(portal_mod, "record_auth_failure", recorder._record("record_auth_failure")),
        patch.object(portal_mod, "dummy_verify", recorder._record("password_hash_cost")),
        patch.object(portal_mod, "verify_password", recorder._record("password_hash_cost")),
        patch.object(portal_mod, "queue_auth_audit", queue or recorder.queue),
    )


async def _attempt_portal_login(recorder: _Recorder, *, vu, queue=None) -> HTTPException:
    from app.api import portal_auth as portal_mod
    from app.schemas.portal import PortalLoginRequest

    db = _db_returning(vu)
    patches = _patched_portal_login(recorder, queue=queue)
    for p in patches:
        p.start()
    try:
        with pytest.raises(HTTPException) as exc:
            await portal_mod.portal_login(
                PortalLoginRequest(
                    email=KNOWN_EMAIL if vu is not None else UNKNOWN_EMAIL,
                    password=SUBMITTED_PASSWORD,
                ),
                _fake_request(),
                slug="acme",
                db=db,
            )
    finally:
        for p in reversed(patches):
            p.stop()
    assert db.execute.await_count == 1
    return exc.value


@pytest.mark.asyncio
async def test_portal_known_and_unknown_rejections_await_the_same_operations():
    """Same deliverable, supplier surface. Pre-fix the known path appends an
    extra awaited `_audit_portal_login_failure` → `dispatch_auth_audit`."""
    known = _Recorder()
    known_exc = await _attempt_portal_login(known, vu=_vendor_user())

    unknown = _Recorder()
    unknown_exc = await _attempt_portal_login(unknown, vu=None)

    assert known.awaited == unknown.awaited
    assert known.awaited == [
        "check_rate_limit",
        "check_auth_failures",
        "password_hash_cost",
        "record_auth_failure",
    ]
    assert known_exc.status_code == unknown_exc.status_code == 401
    assert known_exc.detail == unknown_exc.detail == "Invalid credentials"

    assert len(known.queued) == len(unknown.queued) == 1
    assert known.queued[0]["organization_id"] is not None
    assert unknown.queued[0]["organization_id"] is None
    assert known.queued[0]["action"] == unknown.queued[0]["action"] == "portal.login.failure"


@pytest.mark.asyncio
async def test_portal_legacy_row_without_an_org_lands_on_the_same_branch():
    """A vendor-user row predating `organization_id` has nowhere to route a row
    either. It used to `return` early from inside the audit helper — AFTER the
    await had already been entered; now it reaches the same `None` branch as an
    unknown address, so it is not a third timing class."""
    legacy = _Recorder()
    await _attempt_portal_login(legacy, vu=_vendor_user(org=False))

    unknown = _Recorder()
    await _attempt_portal_login(unknown, vu=None)

    assert legacy.awaited == unknown.awaited
    assert legacy.queued[0]["organization_id"] is None


@pytest.mark.asyncio
async def test_portal_known_address_failure_still_writes_the_audit_row():
    """The row survives the move, and stays PII-lean: the supplier contact is
    identified by `entity_id`, never by address."""
    from app.services import audit_dispatch

    written: list[dict] = []

    async def _write(**kwargs):
        written.append(kwargs)

    vu = _vendor_user()
    recorder = _Recorder()
    with patch.object(audit_dispatch, "_write_auth_audit", _write):
        await _attempt_portal_login(recorder, vu=vu, queue=audit_dispatch.queue_auth_audit)
        await audit_dispatch.drain_auth_audits()

    assert len(written) == 1
    row = written[0]
    assert row["action"] == "portal.login.failure"
    assert row["organization_id"] == vu.organization_id
    assert row["entity_id"] == vu.id
    assert row["details"] == {"ip": "10.0.0.1", "reason": "bad_password"}
    assert KNOWN_EMAIL not in repr(row["details"])


# ---------------------------------------------------------------------------
# 6. The drain is bounded, and wired into shutdown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drain_is_bounded_and_reports_what_it_abandoned(caplog):
    """A hung audit write must cost a graceful shutdown a KNOWN amount.

    Unbounded, one stuck write blocks the shutdown until an orchestrator
    SIGKILLs the process — at which point every *other* queued row is lost too,
    not just the stuck one. So the drain gives up, cancels, and says how many
    rows it abandoned; PII-free (a count and a bound, never who was signing in).
    """
    import asyncio

    from app.services import audit_dispatch

    started = asyncio.Event()

    async def _hangs(**kwargs):
        started.set()
        await asyncio.sleep(3600)

    with patch.object(audit_dispatch, "_write_auth_audit", _hangs):
        audit_dispatch.queue_auth_audit(
            organization_id=uuid.uuid4(),
            actor_id=None,
            action="auth.login.failure",
            details={"email": KNOWN_EMAIL, "ip": "10.0.0.1", "reason": "bad_password"},
        )
        await started.wait()

        with caplog.at_level(logging.WARNING, logger="app.services.audit_dispatch"):
            loop = asyncio.get_running_loop()
            before = loop.time()
            abandoned = await audit_dispatch.drain_auth_audits(timeout=0.1)
            elapsed = loop.time() - before

    assert abandoned == 1
    assert elapsed < 2, "the drain must return on its own bound, not wait out the write"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert "abandoned=1" in joined
    assert KNOWN_EMAIL not in joined

    # The stuck write was cancelled, not left running past shutdown. The task
    # is cancelled synchronously; its done-callback (which unregisters it)
    # runs on a later loop iteration, so yield until the registry drains
    # rather than sleeping a guessed interval.
    for _ in range(100):
        if not audit_dispatch._pending_auth_audits:
            break
        await asyncio.sleep(0)
    assert not audit_dispatch._pending_auth_audits


@pytest.mark.asyncio
async def test_lifespan_shutdown_drains_queued_audit_writes():
    """The rung that makes the documented guarantee hold on a clean shutdown.

    `queue_auth_audit` deliberately does not await, so a process stopping right
    after a rejected login could drop the row. The lifespan's `finally` drains
    it — and must do so BEFORE `dispose_all_engines`, since the write commits
    through the tenant engine that call disposes.
    """
    from app import database as db_mod
    from app import main as main_mod
    from app.services import audit_dispatch

    written: list[dict] = []
    order: list[str] = []

    async def _write(**kwargs):
        import asyncio

        await asyncio.sleep(0.05)  # still in flight when the lifespan exits
        written.append(kwargs)
        order.append("write")

    async def _dispose():
        order.append("dispose")

    with (
        patch.object(audit_dispatch, "_write_auth_audit", _write),
        patch.object(db_mod, "dispose_all_engines", _dispose),
        patch.object(main_mod.settings, "debug", True),
        patch.object(main_mod.settings, "extraction_reaper_enabled", False),
    ):
        async with main_mod.lifespan(main_mod.app):
            audit_dispatch.queue_auth_audit(
                organization_id=uuid.uuid4(),
                actor_id=None,
                action="auth.login.failure",
                details={"email": KNOWN_EMAIL, "ip": "10.0.0.1", "reason": "bad_password"},
            )

    assert len(written) == 1, "shutdown must flush the queued auth audit row"
    assert order == ["write", "dispose"], "the drain must run before the engines are disposed"
