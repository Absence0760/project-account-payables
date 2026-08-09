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

## ~~A dev backend on the same Postgres mutates the pytest tenant DBs mid-test~~ — FIXED 2026-08-08

**Resolved** by giving the `realdb` pytest harness its own control-plane
database per slot (`feohledger_pytest<N>` — slot 0 included) instead of
registering test orgs in the real, shared `feohledger`
(`backend/tests/conftest.py::control_db_name_for_slot`). A dev backend's
background sweeps (`extraction_reaper.run_reaper_loop` et al. —
`select(Organization.id, Organization.db_name)` with no filter against
whatever DB `settings.database_url` names) can no longer discover the
harness's test tenants at all, because their `Organization` rows no longer
live there. This also removes the cross-process contention the original
write-up flagged on the shared control plane's unique constraints (org slugs,
user emails) — concurrent slots now don't share a control-plane database
either.

Kept as a stub because the diagnosis is worth not repeating: the root cause
(background sweeps enumerating every org with no filter), the confirmation
run that ruled out a genuine test-ordering/leak bug, and why the fix landed
as its own change rather than folded into the original slot-claim work all
still apply as background. The current mechanism — naming, one-time-per-slot
provisioning, and the session-start schema self-heal it shares with the
tenant pair — is documented in `backend/CLAUDE.md` § Test databases and in
`control_db_name_for_slot`'s docstring in `backend/tests/conftest.py`.
Regression coverage is `backend/tests/test_realdb_harness.py` (the "control
plane is per-slot" section) — including a direct proof that a session opened
against `settings.database_url` cannot see an `Organization` row the harness
created for its slot.

One thing worth carrying forward: this is a **one-time-per-slot** cost
(provisioning a new database, or self-healing its schema, once per pytest
process — the same accepted trade-off the tenant pair already makes), not a
per-test one. Measured on a single realdb test reaching a cold slot: about 3
seconds slower than before this fix, which does not survive contact with a
multi-thousand-test suite where the fixture's setup is paid once regardless
of how many realdb tests follow.
