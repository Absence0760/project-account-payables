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
