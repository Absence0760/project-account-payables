"""The immediate webhook-delivery task survives garbage collection.

`emit_event` writes the `WebhookDelivery` row and then fires an in-process
delivery attempt with `loop.create_task(...)`. The event loop keeps only a
*weak* reference to a running task, so a task whose only strong reference is
the local variable that created it may be collected mid-`await` — the hazard
the asyncio docs call out. For this dispatcher that means abandoning a delivery
after its row is already written, quite possibly mid-POST to the customer's
endpoint, leaving it `pending` until the retry sweep notices.

`erp_dispatch` and `payment_erp_sync` already hold their fire-and-forget tasks
in a module-level set; this pins the same property for the webhook dispatcher.
"""

from __future__ import annotations

import asyncio
import gc
import uuid
from unittest.mock import patch

import pytest

from app.services.webhooks import dispatch


@pytest.mark.asyncio
async def test_immediate_attempt_task_is_strongly_referenced_until_it_finishes():
    started = asyncio.Event()
    release = asyncio.Event()
    finished: list[uuid.UUID] = []

    async def _slow_delivery(delivery_id):
        started.set()
        await release.wait()
        finished.append(delivery_id)

    delivery_id = uuid.uuid4()
    with patch(
        "app.services.webhooks.delivery.process_delivery_by_id",
        _slow_delivery,
    ):
        dispatch._spawn_immediate_attempt(delivery_id)
        await started.wait()

        # Mid-flight: the dispatcher must be the thing keeping it alive.
        assert len(dispatch._delivery_tasks) == 1
        task = next(iter(dispatch._delivery_tasks))
        assert not task.done()

        # A collection cycle here is exactly what used to reap it.
        gc.collect()
        gc.collect()
        assert not task.done() and not task.cancelled()

        release.set()
        await task

    assert finished == [delivery_id], "the delivery was abandoned mid-flight"
    # And the reference is released, so the set can't grow without bound.
    assert dispatch._delivery_tasks == set()


@pytest.mark.asyncio
async def test_a_failing_delivery_releases_its_reference_and_never_warns():
    """Delivery errors are persisted on the row, not raised — the done callback
    still has to retrieve the exception, or asyncio logs 'exception was never
    retrieved' on collection."""

    async def _boom(delivery_id):
        raise RuntimeError("receiver refused")

    with patch("app.services.webhooks.delivery.process_delivery_by_id", _boom):
        dispatch._spawn_immediate_attempt(uuid.uuid4())
        assert len(dispatch._delivery_tasks) == 1
        task = next(iter(dispatch._delivery_tasks))
        with pytest.raises(RuntimeError):
            await task

    # Let the done callback run.
    await asyncio.sleep(0)
    assert dispatch._delivery_tasks == set()
    # The callback retrieved it, so nothing is left to warn about.
    assert task.exception() is not None


@pytest.mark.asyncio
async def test_no_running_loop_falls_back_to_the_sweep():
    """Called from a sync worker thread there is no loop to schedule on; the
    durable retry sweep owns the delivery instead of it being dropped."""

    def _from_a_thread():
        dispatch._spawn_immediate_attempt(uuid.uuid4())

    await asyncio.to_thread(_from_a_thread)
    assert dispatch._delivery_tasks == set()
