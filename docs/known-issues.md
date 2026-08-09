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

## ~~Read-after-write race on every mutating endpoint~~ — FIXED 2026-08-06

**Resolved** by `commit_before_response` (`backend/app/database.py`), applied by
both session providers. The success-path commit now runs on the exit stack
FastAPI unwinds *before* `await response(scope, receive, send)`, so a `201` is
no longer returned for an uncommitted write. The post-`yield` commit remains as
a conditional backstop.

Kept as a stub because the diagnosis is worth not repeating: the root cause,
everything ruled out on the way to it (middleware, pool staleness, engine-cache
races), and why the fix is shaped the way it is, now live in
[decisions.md §20](decisions.md). Regression coverage —
`backend/tests/test_commit_before_response.py` — pins the *ordering* invariant
and drift-guards the FastAPI internal it relies on.

One finding is worth carrying forward: the documented network repro (rapid
create-then-read pairs) did **not** reproduce over loopback even while the defect
was measurably present, because server and middleware pacing decide whether a
client can observe it. Don't treat "the repro passes" as evidence this class of
bug is absent — measure the ordering instead.

---

## ~~Workflow-mutating e2e specs can strand a tenant on a disabled workflow definition~~ — RESOLVED 2026-08-08

**Audit:** every spec that mutates a workflow definition's `is_active`, step
`enabled` flags, or `is_default` — `tests-e2e/workflows/*.spec.ts`,
`tests-e2e/workflow-builder.spec.ts`, and the two files that touch the live
default in passing (`admin/delete-safety.spec.ts`,
`invoices/daily-journey.spec.ts`) — already wraps its mutation in a
`try/finally` that restores the exact prior state, or scopes itself to a
throwaway definition it creates and deletes. No spec needed a code fix; the
original diagnosis's "afterAll doesn't restore" theory didn't match what's on
disk today.

**What was actually missing** was the doc's own second recommendation: a cheap
guard for the residual risk the try/finally pattern can't close on its own — a
hard interruption (killed process, machine crash, a timed-out test whose
continuation never gets scheduled) skipping the `finally` block entirely.
Added `frontend/tests-e2e/fixtures/globalSetup.ts`, wired into
`tests-e2e/playwright.config.ts`'s `globalSetup`: before any test/worker
starts, it asserts every tenant (`acme`, `techflow`, `e2e1..N`) has exactly one
`is_default=true` workflow definition, `is_active=true`, with its `approval`
and `erp_export` steps enabled — the shape `backend/scripts/seed.py` creates.
A miss throws one clear, tenant-and-field-named error instead of a confusing
409 three specs later.

**The guard is proven, not just written**: this local dev Postgres had
genuinely accumulated the exact symptom the original diagnosis described —
`feoh_e2e1` and `feoh_e2e3` each carried a shared-scope `is_default=true`
"Invoice Processing" stub (all steps disabled) alongside the entity-scoped
`Default Workflow`. The new guard flagged both, by name, before any test ran.
Cleaned up (no `workflow_instances` referenced either stub, so a plain
`DELETE` restored the fresh-seed shape) and re-ran the guard clean. Then ran
the full `tests-e2e/workflows/` + `workflow-builder.spec.ts` suite (52 tests)
twice back-to-back against `e2e1` — all green both times, and
`workflow_definitions` for that tenant was bit-for-bit identical (one
`Default Workflow`, `is_active=t`, all three steps `enabled:true`) before,
between, and after both runs.

One thing worth carrying forward: the stray "Invoice Processing" stub wasn't
traceable to any spec in the current suite — none of them create a window
where zero workflows are active while also creating a new invoice with no
entity-scoped default in play, which is what the auto-create fallback needs to
fire. It most likely came from manual UI exploration against a long-lived
dev tenant, not test-run drift. The guard doesn't care which — it catches the
*state*, not the cause — but if it ever fires again, check for a new code path
that can leave zero active workflows before assuming it's the old bug back.

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
