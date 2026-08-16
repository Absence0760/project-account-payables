# Background sweeps — health, supervision, and the shared loop runner

Fourteen long-lived asyncio tasks are started in `app/main.py`'s lifespan, each
behind its own `FEOH_*_ENABLED` gate. They are enumerated with what they do in
[`backend/CLAUDE.md`](../CLAUDE.md) § Key background services. This document
covers the machinery they all share: how a tick's outcome is recorded, how a
dead sweep is detected, and how an operator reads either.

## The problem this closes

Every sweep used to carry its own copy of the same loop:

```python
while True:
    try:
        await <sweep>_once()          # <-- return value DISCARDED
    except Exception as exc:
        logger.error("[x] sweep raised: %s", exc.__class__.__name__)
    await asyncio.sleep(interval)
```

Twelve of the fourteen `*_once` functions already returned a result dataclass
carrying a `failures: int`, and **every loop threw it away at the call site**.
The counter's only consumer was a conditional aggregate `logger.info` inside
`*_once` itself: never persisted, never exposed, never alerted on. There was
also no supervision — `asyncio.create_task` with no `add_done_callback` — and
`GET /api/health` returned a static `ok` that said nothing about whether any
sweep was alive.

The consequence, and the reason this was worth fixing: an `audit_shipping` sink
misconfigured for months (the adapters raise by design, so rows stay unshipped
and retry forever — the SOC 2 WORM evidence trail simply isn't leaving the
tenant DB) looked *identical* to one running clean.

## The mechanism — one, not fourteen

`app/services/sweep_health.py` owns all of it:

| Piece | What it does |
|-------|--------------|
| `run_sweep_loop(name, tick, …)` | The single loop body every `run_*_loop` delegates to. Ticks, records the outcome, sleeps, re-raises `CancelledError` on shutdown. |
| The registry | Per-sweep last-run timestamps, last outcome, consecutive-failure streak, lifetime run counts, and the integer counters the sweep's own result dataclass reported. |
| `supervise_task(task)` | The `add_done_callback` that records a sweep task ending — cleanly cancelled, or **died**. |
| `extract_counts` / `failure_count` | Pure helpers turning any sweep's result into PII-free counters. |

A `run_*_loop` is now four lines:

```python
async def run_reaper_loop() -> None:
    await run_sweep_loop(
        SWEEP_EXTRACTION_REAPER,
        lambda: reap_once(),
        interval_seconds=settings.extraction_reaper_interval_seconds,
        log=logger,
        log_prefix="[reaper]",
    )
```

Two details are load-bearing:

- **`tick` is a zero-arg callable that resolves `*_once` at call time**
  (`lambda: reap_once()`, never a captured `reap_once` reference). The suites
  patch the module attribute, and a captured reference would sail past the
  patch.
- **`log` is the calling module's logger**, so per-sweep log filters keep
  working and a failure is attributed to the sweep that had it. Only the loop
  *body* is shared, not the log identity.

## What counts as a failed run

Two things, and the first is the one the follow-up existed for:

1. **The tick completed but reported failures** (`failures > 0` on its result,
   or any `*_failures` counter — `vendor_rescreen` counts `vendor_failures`
   apart from `failures`). Outcome `partial`, logged at WARNING.
2. **The tick raised.** Outcome `error`, logged at ERROR with the exception
   **class**.

Either increments `consecutive_failures`; a clean run resets it to zero. Once
the streak reaches `FEOH_SWEEP_FAILURE_ALERT_STREAK` (default 3) the sweep is
reported `degraded` and the loop emits the alertable line:

```
[audit-shipper] NOT MAKING PROGRESS: 3 consecutive failed runs (last error class: ClientError)
```

It is emitted on each *multiple* of the streak, not every tick — a 60-second
sweep stuck for a day would otherwise write 1440 identical lines and the signal
an operator alarms on has to stay greppable.

## PII discipline

Only an exception's **class name** is ever recorded or logged, and never with
`exc_info`. The stdlib logging module appends the full traceback — including
`str(exc)` — whenever `exc_info` is passed, regardless of what the format string
names, so passing it leaks exactly the vendor names and account fragments the
PII-out-of-logs invariant exists to exclude. `payment_reconciler` had already
diagnosed this and fixed it for itself; six other loops still passed
`exc_info=True` and two called `logger.exception`. All fourteen now share the
reconciler's posture.

Recorded counters are integers pulled off the sweep's own result dataclass by
`extract_counts`, which keeps **only** `int` fields (and excludes `bool`). A
future result field holding a slug, a name or a message cannot be lifted into
the health payload by accident.

## Reading it: `GET /api/health/sweeps`

Admin-gated (`require_roles(ROLE_ADMIN)`), no tenant DB access, PII-free.

```json
{
  "state": "degraded",
  "failure_alert_streak": 3,
  "sweeps": [
    {
      "name": "audit-log-shipper",
      "state": "idle",
      "enabled": true,
      "started_at": "2026-08-16T09:00:00Z",
      "last_run_started_at": "2026-08-16T11:04:00Z",
      "last_run_finished_at": "2026-08-16T11:04:02Z",
      "last_outcome": "partial",
      "last_error_class": null,
      "last_failure_count": 4,
      "consecutive_failures": 37,
      "total_runs": 128,
      "total_failed_runs": 37,
      "exit_error_class": null
    }
  ]
}
```

Per-sweep `state` is one of:

| State | Meaning |
|-------|---------|
| `disabled` | Its `FEOH_*_ENABLED` flag is off in this process. Expected, not a problem. |
| `not_started` | **Enabled but never registered.** Flips the aggregate to `failing`. |
| `starting` / `running` / `idle` | Registered; a tick is pending, in flight, or done. |
| `stalled` | A tick has been in flight far past the sweep's own cadence — see below. |
| `stopped` | Cancelled cleanly at shutdown. |
| `died` | The task ended on its own — a real defect. It is gone for the life of the process. |

The aggregate `state` is `failing` if any sweep died or is enabled-but-absent,
`degraded` if any is past its streak or stalled, else `ok`.

### The hung-tick hole, and `stalled`

Failure counting alone leaves one case invisible: a sweep **hung inside**
`*_once` — a DB connect with no timeout, an adapter socket that never returns —
never raises, never completes, and so never touches the streak. It sits in
`running` forever while reporting perfectly healthy, which is precisely the
"alive but not progressing" state this whole mechanism exists to expose.

`stalled` closes it. It is **derived on read**, not stored — nothing is
executing while a tick hangs, so there is no code path that could write it. A
tick is stalled once it has been in flight longer than
`max(STALL_FACTOR × interval, STALL_FLOOR_SECONDS)` (3× and 15 minutes; module
constants in `sweep_health.py`, not env knobs — the tunable dial is the failure
streak). The threshold is deliberately generous so a legitimately long tick (the
audit shipper draining a large backlog) is not called stalled: a 60-second sweep
gets the 15-minute floor, a daily one gets three days, by which point a tick
still in flight is unambiguously hung.

Stalled reports `degraded` rather than `failing` because a very long tick has a
plausible benign explanation; a dead task has none.

### What the payload deliberately omits

**No cross-tenant cardinality.** An ordinary tenant admin holds `ROLE_ADMIN`, so
returning the raw counters (`tenants_scanned`, `rows_shipped`, …) would tell
them how many organizations the platform sweeps. Only `last_failure_count`
crosses the boundary — the actionable number, and zero on a healthy platform.
The full counters stay in the registry and in the logs, where the reader is a
platform operator who is already trusted with them.

### `GET /api/health` is unchanged

It stays the public, unauthenticated, static `{"status": "ok"}` liveness probe,
and it deliberately does **not** fold in sweep health. A degraded background
sweep is not a reason to pull a healthy process out of rotation; wiring it in
would turn "the audit shipper's sink is misconfigured" into a rolling restart
loop that fixes nothing.

## Scope: per-process, in-memory

State resets on restart, and with several replicas each answers for itself.
That is deliberate:

- A durable per-sweep run table would be an Alembic migration fanned out to
  **every tenant DB** for telemetry that is platform-level, not tenant-level.
- There is no platform-scoped settings row to hang a JSON marker off;
  `Organization.settings` is per-tenant, and these sweeps span all tenants.
- "Is *this* process's sweep alive and progressing?" is the question an operator
  actually asks of a suspect replica, and it is exactly what a per-process
  registry answers.

The cluster-wide view is the log sink: every replica emits the same PII-free
`NOT MAKING PROGRESS` line, which is the alertable signal in a deployed
environment.

## Drift guards

`tests/test_sweep_health.py` pins three things a rename would otherwise break
silently:

1. Every `asyncio.create_task(..., name="…")` / `start_sweep(..., name="…")`
   string in the lifespan is a canonical sweep name — the task name **is** the
   registry key.
2. Every lifespan sweep is started via `start_sweep()`, so none can go back to
   being unsupervised.
3. Every entry in `SWEEP_ENABLED_FLAGS` names a real `Settings` attribute.

## Adding a sweep

1. Write `<sweep>_once()` returning a result dataclass with a `failures: int`.
2. Add a `SWEEP_<NAME>` constant and its `SWEEP_ENABLED_FLAGS` entry in
   `sweep_health.py`.
3. Write `run_<sweep>_loop()` as the four-line delegation above.
4. Start it in the lifespan with `start_sweep(run_<sweep>_loop(), name=…)`
   behind its `FEOH_*_ENABLED` gate, and cancel it in the `finally`.
5. Document it in `backend/CLAUDE.md` § Key background services and its env vars
   in `docs/environment.md`.

The drift guards fail until steps 2 and 4 agree.
