"""bcrypt never runs on the event loop, and nothing bypasses the wrappers.

bcrypt is *deliberately* expensive — roughly 200 ms of pure CPU per verify at
the configured cost. That is the whole point of the algorithm and it is also
why calling `pwd_context.verify` inline from a login handler is a defect: for
those 200 ms the worker's event loop is occupied and every other in-flight
request waits. `/auth/login` and the supplier-portal login are the most
concurrently-hit endpoints in the app, and the not-found branch pays the same
cost again through `dummy_verify` (deliberately, for timing equalisation), so
the stall is charged to *every* login attempt, valid or not.

`app/utils/passwords` therefore exposes `verify_password` / `hash_password` /
`dummy_verify` as coroutines that run the work through `asyncio.to_thread`.
These tests pin that the work really leaves the loop thread, that the
timing-equalisation contract survives the change, and that no module under
`app/` calls the blocking context directly.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import pathlib
import threading

import pytest

from app.utils import passwords
from app.utils.passwords import dummy_verify, hash_password, pwd_context, verify_password

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

# The blocking passlib operations. `app/utils/passwords.py` owns them; every
# other module goes through the awaitable wrappers.
BLOCKING_OPS = {"verify", "hash"}
OWNER = APP_DIR / "utils" / "passwords.py"

_HASH = pwd_context.hash("Correct-Horse-1234")


@pytest.mark.asyncio
async def test_verify_and_hash_run_off_the_event_loop_thread():
    loop_thread = threading.current_thread().ident
    seen: list[int | None] = []
    real_verify = pwd_context.verify
    real_hash = pwd_context.hash

    def _rec_verify(*args, **kwargs):
        seen.append(threading.current_thread().ident)
        return real_verify(*args, **kwargs)

    def _rec_hash(*args, **kwargs):
        seen.append(threading.current_thread().ident)
        return real_hash(*args, **kwargs)

    orig_verify, orig_hash = pwd_context.verify, pwd_context.hash
    try:
        pwd_context.verify = _rec_verify
        pwd_context.hash = _rec_hash
        assert await verify_password("Correct-Horse-1234", _HASH) is True
        assert await verify_password("wrong", _HASH) is False
        assert (await hash_password("Another-Pass-9876")).startswith("$bcrypt-sha256$")
        await dummy_verify()
    finally:
        pwd_context.verify = orig_verify
        pwd_context.hash = orig_hash

    assert len(seen) == 4
    assert all(tid != loop_thread for tid in seen), (
        "bcrypt ran on the event loop thread — every concurrent request waits "
        "~200 ms behind each login attempt"
    )


@pytest.mark.asyncio
async def test_the_loop_keeps_running_while_a_password_is_verified():
    """The point of the offload, stated as behaviour: other coroutines progress.

    Inline bcrypt lets a concurrent coroutine tick exactly zero times, because
    the loop never gets control back until it returns.
    """
    ticks = 0
    stop = False

    async def _heartbeat():
        nonlocal ticks
        while not stop:
            ticks += 1
            await asyncio.sleep(0)

    beat = asyncio.create_task(_heartbeat())
    await asyncio.sleep(0)
    assert await verify_password("Correct-Horse-1234", _HASH) is True
    stop = True
    await beat

    assert ticks > 1, "the event loop made no progress during the verification"


@pytest.mark.asyncio
async def test_dummy_verify_still_costs_what_a_real_verification_costs():
    """The enumeration defence: the not-found branch must not return early.

    `dummy_verify` exists so a login for an unknown address takes as long as one
    with a wrong password. Both now take a thread hop plus a full bcrypt, so the
    guarantee survives the offload — asserted as "the same order of magnitude",
    never as a tight bound, so this can't become a timing-flake.
    """
    loop = asyncio.get_running_loop()

    start = loop.time()
    await dummy_verify()
    dummy_cost = loop.time() - start

    start = loop.time()
    await verify_password("wrong-password", _HASH)
    real_cost = loop.time() - start

    assert dummy_cost > 0
    assert 0.2 < dummy_cost / real_cost < 5.0, (
        f"dummy_verify ({dummy_cost:.4f}s) diverged from a real verification "
        f"({real_cost:.4f}s) — the login-timing enumeration gap is back"
    )


def test_the_public_password_helpers_are_coroutines():
    assert inspect.iscoroutinefunction(verify_password)
    assert inspect.iscoroutinefunction(hash_password)
    assert inspect.iscoroutinefunction(dummy_verify)
    # Not a coroutine — pure CPU-free string checks, correctly left sync.
    assert not inspect.iscoroutinefunction(passwords.validate_password_complexity)


def test_no_module_calls_the_blocking_hash_context_directly():
    """Nothing under `app/` reaches around the thread offload."""
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path == OWNER:
            continue
        source = path.read_text()
        if "pwd_context" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in BLOCKING_OPS
                and isinstance(func.value, ast.Name)
                and "pwd" in func.value.id.lower()
            ):
                offenders.append(
                    f"{path.relative_to(APP_DIR.parent)}:{node.lineno} .{func.attr}(...)"
                )

    assert offenders == [], (
        "password hashing must go through `verify_password` / `hash_password` "
        "(which offload bcrypt to a thread); found: " + ", ".join(offenders)
    )
