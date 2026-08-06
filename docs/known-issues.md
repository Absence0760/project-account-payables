# Known Issues

Tracked, non-trivial defects that are diagnosed but not yet fixed. Each entry
names the root cause, the evidence, blast radius, and a recommended fix
approach — this is a staging area for real problems, not a place to let them
go stale. See root `CLAUDE.md` guard rail 6 (no dangling deferred findings).

**Scope:** *diagnosed defects* only. A deferral that isn't a defect — blocked on
a credential, an operator step on merged code, or sized-but-unstarted work —
goes to [followups.md](followups.md). Reasoning behind a deliberate design call
goes to [decisions.md](decisions.md).

---

## Read-after-write race on every mutating endpoint (write commits *after* the response is sent)

**Discovered:** 2026-07-03, while verifying the Invoice PDF Management feature
(`PUT`/`DELETE /api/invoices/{id}/file`). Not caused by that feature — it's a
pre-existing, app-wide issue that rapid scripted requests happen to expose.

**Symptom:** A client can receive a success response (e.g. `201 Created` from
`POST /api/invoices`) for a write whose transaction has not actually committed
yet. A sufficiently fast follow-up request reading the same row (a plain
`GET /api/invoices/{id}`, or any subsequent mutation on it) can get a `404`,
even though the first request already returned success.

**Reproduced with a raw script, bypassing the browser/test framework entirely**
(no PDF-management code involved — just the pre-existing `POST /api/invoices`
+ `GET /api/invoices/{id}`):

```python
# 30 rapid create-then-read pairs against a fresh backend process
# → 15-16 failures (roughly 50%)
create = await c.post("/api/invoices", json={...}, headers=headers)
inv_id = create.json()["id"]
get = await c.get(f"/api/invoices/{inv_id}", headers=headers)
# get.status_code is 404 "Invoice not found" on ~half the iterations
```

Inserting a 50ms delay between the two calls made the failures disappear
entirely (0 of 30) — confirming this is a pure timing race, not a logic bug
in either endpoint.

**Root cause — confirmed by reading the installed FastAPI's own source**
(`fastapi/routing.py`, v0.139.0, `get_request_handler`'s inner `app` function):

```python
async with AsyncExitStack() as request_stack:
    scope["fastapi_inner_astack"] = request_stack
    async with AsyncExitStack() as function_stack:
        scope["fastapi_function_astack"] = function_stack
        response = await f(request)          # route handler runs here
    await response(scope, receive, send)      # <-- response SENT to client here
    response_awaited = True
# request_stack.__aexit__ runs AFTER the line above returns —
# this is where a `Depends(yield)`'s post-yield code executes.
```

`db: AsyncSession = Depends(get_tenant_db)` (and `get_control_db`) resolve
against `request_stack` (`fastapi_inner_astack`). Their post-`yield` code —
`await session.commit()` in `backend/app/tenant.py::get_tenant_db` and
`backend/app/database.py::get_control_db` — is exactly the code that runs in
`request_stack.__aexit__`. Per the snippet above, that only happens **after**
`await response(scope, receive, send)` — i.e. after the response has already
been hand off to the ASGI server (uvicorn) to send to the client. This is
FastAPI's documented behavior for dependencies with `yield`, not a bug in
FastAPI itself — but this app's session-management pattern (every mutating
route relies on the *dependency's* post-yield commit rather than an explicit
in-handler commit) inherits the race as a result.

**Ruled out before landing on the above** (in case a future investigation
starts from a different symptom and wants to skip re-treading this ground):

- **Long-running dev server accumulating cruft** — restarted the backend
  process fresh; the race still reproduced immediately on iteration 3-4.
- **Connection-pool / leaked-transaction staleness** — polled
  `pg_stat_activity` during a live repro; no `idle in transaction` sessions,
  no connections with an old `xact_start`. Every connection was cleanly
  `idle` with `COMMIT`/`ROLLBACK` as its last statement.
- **`starlette.middleware.base.BaseHTTPMiddleware`** (`SecurityHeadersMiddleware`
  in `backend/app/main.py`) — `BaseHTTPMiddleware` has its own well-known,
  *different* timing bug (runs the downstream app in a background
  `asyncio.Task`), so this looked like a plausible cause. Converted it to
  pure ASGI middleware as a test — **made no difference** (root-caused via the
  FastAPI source above: no middleware, ASGI or `BaseHTTPMiddleware`, can fix
  this, because the response is fully sent from *inside* FastAPI's own
  per-route `app` callable, before any wrapping middleware regains control).
  The middleware conversion was reverted since it didn't address the actual
  bug and would have been unreviewed scope creep.
- **Duplicate tenant engines from a `get_tenant_engine` cache race** — logged
  the cached engine's `id()` across every request in a repro run; identical
  every time. Ruled out.

**Blast radius:** every endpoint that both (a) mutates data through
`get_tenant_db`/`get_control_db`'s implicit post-yield commit (the standard
pattern used almost everywhere in this codebase — grep for
`Depends(get_tenant_db)` / `Depends(get_control_db)` — this is *not* a short
list) and (b) is followed quickly enough by a request reading the same row.
In practice: any UI flow that creates-then-immediately-reopens a record,
double-click-guards that re-fetch after a mutation, or (most acutely) any
Playwright e2e spec that drives two dependent calls back-to-back via
`page.request` with no natural pacing between them. Real browser-driven UI
flows (click → wait for modal → fill fields → submit) have enough incidental
async/render/network latency between actions that they're far less likely to
land inside the race window than a raw scripted API sequence — but the window
is real and this is a financial app; a lost-but-acknowledged write is a
genuine durability concern, not just an intermittent test flake.

**Recommended fix (not yet implemented — this is the deferral):** stop relying
on the dependency's post-`yield` commit for the success path. The two
standard options:

1. **Explicit commit in every mutating handler**, immediately before
   returning, instead of relying on `get_tenant_db`/`get_control_db`'s
   teardown. Correct and simple per-endpoint, but touches every mutating
   route across the codebase (dozens of files) — large, mechanical, but
   individually low-risk changes. Post-yield `rollback()` stays as the
   exception-path backstop only.
2. **ASGI-level commit-before-send**: keep the dependency as a thin session
   provider (no commit/rollback of its own), store the session on a
   contextvar or `request.state`, and commit it from a pure ASGI middleware's
   wrapped `send` callable, right before forwarding the
   `http.response.start` message. This is the well-known workaround pattern
   for this exact FastAPI limitation — one central change (the middleware +
   the two session-provider dependencies) rather than touching every route,
   but needs careful handling of routes that never touch the DB, routes that
   use both tenant and control sessions in one request, and WebSocket/SSE
   paths that don't have a single terminal response.

Either approach needs full regression coverage (the existing pytest suite
exercises `create_invoice`-then-read patterns implicitly via `realdb.client()`,
which doesn't hit this race today because pytest awaits each call in-process
with no comparable timing pressure — the fix needs its own dedicated
concurrency-focused tests, e.g. a tight create-then-read loop like the repro
script above, made a permanent regression test).

**Trigger to revisit:** before this bug causes a real production incident
(a user-visible "your invoice disappeared" report), or before it destabilizes
CI (a flaky e2e spec that traces back to this same root cause is a strong
signal to prioritize the fix over patching the symptom again).

---

## Workflow-mutating e2e specs can strand a tenant on a disabled workflow definition

**Discovered:** 2026-07-17, while stabilizing the new `tests-e2e/erp/` suite —
its netsuite spec flaked with a 409 on `/api/invoices/{id}/approve` only when
its Playwright worker landed on the `e2e3` tenant.

**Symptom:** on a long-lived local dev database, a tenant's genuine seeded
"Default Workflow" can end up `is_active = false`, leaving the auto-created
"Invoice Processing" stub (every step `enabled: false`) as the governing
definition. Any spec (or user) that then relies on the approval / erp_export
steps being enabled gets surprising transitions: `POST /complete` walks
`new → done` (no approval step), so a follow-up `/approve` 409s.

**Evidence:** local `feoh_e2e3` had `Default Workflow (entity-scoped,
is_default=t, is_active=f)` + `Invoice Processing (shared, is_default=t,
is_active=t, all steps disabled)`; `feoh_e2e1/2/4` were healthy. The state is
left behind by workflow-mutating suites (`workflows/`, `workflow-builder`)
whose cleanup doesn't restore `is_active` / step-enabled flags on the seeded
default. CI is unaffected today (every run seeds fresh, and the `erp-e2e` job
runs only `erp/`), but a within-shard ordering that runs a workflow suite
before an invoice-flow suite could reproduce it in CI.

**Blast radius:** local e2e flakiness for any suite that assumes the seeded
workflow shape (`invoices/lifecycle-money-path`, `purchase-orders/sync`, …);
confusing local-dev behavior when clicking through the same tenant. The
`erp/` suite is immune since 2026-07-17 — its helper approves directly via the
legal `new → approved` edge instead of relying on `/complete`.

**Recommended fix:** audit the workflow-mutating specs' `afterAll` blocks to
restore the definitions they touched (`is_active`, step `enabled` flags,
`is_default`), and prefer creating throwaway definitions over mutating the
seeded default. A cheap guard: a fixture assertion (or `seed.py --verify`
mode) that the tenant's governing definition has approval + erp_export
enabled before suites that depend on it run.

**Trigger to revisit:** the next local flake that traces to workflow state, or
any plan to run multiple suite directories in one CI shard sequentially.

---

## A dev backend on the same Postgres mutates the pytest tenant DBs mid-test

**Discovered:** 2026-07-20, while root-causing the concurrent-pytest cross-kill
(issue #211's environment notes). **Confirmed:** 2026-07-22.

**Symptom:** realdb pytest failures — missing or unexpectedly-`failed`
invoices, `TRUNCATE` stalls — in a run that overlapped a locally-running
backend (`pnpm dev:backend`, or the backend Playwright drives) pointed at the
same Postgres.

**Confirmation (2026-07-22):** issue #211 reported ~23 failures (in
`test_access_reviews.py`, `test_adaptive_workflows*.py`,
`test_analytics_aging_reconciliation.py`, `test_assistant*.py`,
`test_audit_access.py`, `test_partner_admin.py`) from a single whole-suite
`pytest -q` run, with two confounds the reporter flagged themselves: a
locally-running dev backend and a Playwright e2e run against the same
Postgres. Re-ran the identical whole-suite `pytest -q` with **both stopped**
and nothing else attached to Postgres (a clean ~2h16m serial run, single
process, no sharding): **zero failures in any of the originally-reported
files.** The only failures in that clean run (68, all `UndefinedColumnError`)
were an unrelated, separately-diagnosed local schema-drift issue (see the next
entry) — not this one. This is strong evidence the original ~23 failures were
caused by the confound, not a genuine whole-suite test-ordering/leak bug.

**Root cause:** the backend's background sweeps enumerate **every** organization
in the control plane and open a connection per tenant DB —
`extraction_reaper.run_reaper_loop` does
`select(Organization.id, Organization.db_name)` with no filter, and it is on by
default (`FEOH_EXTRACTION_REAPER_ENABLED` defaults to `True`). The realdb harness
registers its test tenants in that same shared control plane
(`feohledger`), so a dev server happily sweeps `feoh_pytesta` / `feoh_pytestb`
— transitioning stuck `pending` invoices to `failed` inside a database a test is
mid-way through asserting on, and holding a snapshot/lock the next test's
`TRUNCATE` must wait for.

The per-process slot claim added in `tests/conftest.py` (see backend
`CLAUDE.md` § Test databases) makes *pytest processes* mutually exclusive, but it
cannot hide the harness tenants from a dev server, which discovers them through
the control plane rather than by name.

**Blast radius:** local only, and only while a backend is running against the
same Postgres as a pytest run. CI is immune — each shard boots its own Postgres
and runs no server.

**Workaround today:** don't run the dev backend against the same Postgres while
running realdb pytest (stop `pnpm dev:backend`, or point one of them at another
instance).

**Recommended fix:** give the harness its own control-plane database per slot
(`feohledger_pytest<N>`) instead of sharing `feohledger`, so the
harness tenants are invisible to any server on the default control plane. That
also removes the remaining cross-process contention on the shared control-plane
unique constraints (emails, org slugs). Deferred here because it moves
`settings.database_url` for the whole test session — a materially wider blast
radius than the isolation fix it would extend, and worth landing on its own.

**Trigger to revisit:** the next realdb failure that can't be reproduced with
nothing else attached to Postgres, or any move to run the e2e stack and pytest
concurrently.
