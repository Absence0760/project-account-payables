"""Run best-effort side effects AFTER the caller's transaction commits.

**The problem.** ``workflow_engine.transition_invoice`` mutates the status,
writes the audit row, and then ``await``\\ s the notification fan-out — one email
per recipient, serially, followed by a Slack/Teams POST with a 10-second
``httpx`` timeout. None of that is committed work; the caller commits
afterwards. So a third party's wall-clock latency is charged to an OPEN
transaction, and the two call sites that matter hold row locks across it:
``payment_erp_sync._sync_one_payment`` takes the invoice ``SELECT … FOR UPDATE``
and only commits after the transition returns, and ``review.approve_invoice``
holds ``FOR UPDATE`` on the ``WorkflowInstance``. A hung Slack webhook therefore
held a row lock on a live invoice for up to ten seconds, and N recipients
multiplied the email leg linearly.

**The fix, and why it is shaped like this.** The outbound legs are queued onto
the caller's *session* and fired from SQLAlchemy's ``after_commit`` event —
which runs after the DB has actually committed, so every lock the transaction
held is already released. Nothing at the ~35 ``transition_invoice`` call sites
changes: they keep committing exactly as they do today, and the ordering
guarantee they depend on ("in-app ``Notification`` rows ride the caller's
commit") is untouched, because only the email/chat legs move. Work queued by a
transaction that ROLLS BACK is dropped, which is the correct new semantics: we
no longer email people about a status change that never happened.

**Two execution modes**, and the second is not a fallback for convenience:

* On the app's event loop, jobs are spawned as tasks (strong-referenced until
  done, mirroring ``services/webhooks/dispatch``) so the request returns
  immediately.
* Under a :func:`app.database.dispatch_engine_scope` — a dispatcher worker's
  own short-lived loop, which closes the moment the job returns — a spawned
  task would be abandoned mid-flight, so the jobs are awaited INLINE via
  SQLAlchemy's ``await_only`` (valid here: ``after_commit`` runs inside the
  greenlet that ``await session.commit()`` spawned). That still fixes the
  hazard: the COMMIT has already happened, so no lock is held while it runs.

Best-effort throughout: a job that raises is logged by CLASS NAME only and
never propagates. The transports here raise exceptions that embed exactly what
must not be logged — httpx's ``HTTPStatusError`` carries the request URL, which
for an incoming webhook IS the credential, and ``SMTPRecipientsRefused`` carries
the addresses — so this module never calls ``logger.exception``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import event
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Key under which the pending jobs live on `Session.info`. A list of
# `(name, factory)`; `factory` is a zero-arg callable returning the coroutine to
# run. Deliberately a FACTORY, not a coroutine object: a queued coroutine that
# is dropped (rollback) would emit a "coroutine was never awaited" warning.
_JOBS_KEY = "_feoh_post_commit_jobs"

# Strong references to in-flight tasks. Without this the only reference is the
# local in `_spawn`, and the event loop's own registry is weak — the task can be
# garbage-collected mid-send.
_tasks: set[asyncio.Task] = set()


def enqueue_post_commit(
    session,
    factory: Callable[[], Awaitable[None]],
    *,
    name: str,
) -> None:
    """Queue `factory` to run once `session`'s transaction commits.

    `session` may be an `AsyncSession` (its `.info` proxies to the underlying
    `Session.info`) or a plain `Session`. Never raises: a session that cannot
    carry the queue runs the job... nowhere, which is the same best-effort
    contract the notification path already has.
    """
    try:
        info = session.info
    except Exception:  # noqa: BLE001 — a caller with no usable session
        logger.warning("post-commit: session has no info mapping; dropping job=%s", name)
        return
    info.setdefault(_JOBS_KEY, []).append((name, factory))


def _drain(session: Session) -> list[tuple[str, Callable[[], Awaitable[None]]]]:
    jobs = session.info.pop(_JOBS_KEY, None)
    return list(jobs) if jobs else []


async def _guarded(name: str, factory: Callable[[], Awaitable[None]]) -> None:
    try:
        await factory()
    except Exception as exc:  # noqa: BLE001 — best-effort; class name only, no PII
        logger.warning("post-commit job failed job=%s error=%s", name, exc.__class__.__name__)


def _finish_task(task: asyncio.Task) -> None:
    """Release the strong reference and retrieve any exception."""
    _tasks.discard(task)
    if not task.cancelled():
        task.exception()


def _run_jobs(jobs: list[tuple[str, Callable[[], Awaitable[None]]]]) -> None:
    from app.database import in_dispatch_scope

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and not in_dispatch_scope():
        for name, factory in jobs:
            task = loop.create_task(_guarded(name, factory), name=f"post-commit-{name}")
            _tasks.add(task)
            task.add_done_callback(_finish_task)
        return

    # No loop we may safely outlive. Run inline — still after the COMMIT, so no
    # lock is held; the cost is latency on a background worker, not a request.
    from sqlalchemy.util import await_only

    for name, factory in jobs:
        try:
            await_only(_guarded(name, factory))
        except Exception as exc:  # noqa: BLE001 — e.g. MissingGreenlet on a sync session
            logger.warning(
                "post-commit job could not run job=%s error=%s", name, exc.__class__.__name__
            )


@event.listens_for(Session, "after_commit")
def _on_after_commit(session: Session) -> None:
    jobs = _drain(session)
    if jobs:
        _run_jobs(jobs)


@event.listens_for(Session, "after_rollback")
def _on_after_rollback(session: Session) -> None:
    # The transition never happened — do not tell anyone it did.
    _drain(session)


@event.listens_for(Session, "after_soft_rollback")
def _on_after_soft_rollback(session: Session, previous_transaction) -> None:  # noqa: ANN001
    if not session.is_active:
        _drain(session)


async def drain_post_commit() -> None:
    """Await every post-commit job currently in flight.

    For TESTS that assert on an outbound side effect (an email adapter spy, a
    chat webhook) right after a commit: the jobs are real tasks now, so without
    this the assertion races them. Production code never needs it — the whole
    point is that the caller does not wait.
    """
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)
