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

## A non-generatable recurring template silently skips every period

**Found:** 2026-08-15, in the background-sweep survey behind the
`payment_erp_sync` / `vendor_rescreen` fixes.

`services/recurring_invoices.generate_one` returns `None` for a template missing
`amount` or `vendor_name`, logging a `WARNING` and nothing else. The sweep's
"defensive" cursor advance then rolls `next_run_on` forward **anyway**
(`recurring_invoices.py`, the `if template.next_run_on is not None and
template.next_run_on <= run_on:` block near the end of `_sweep_tenant`).

The guard itself is right — it exists so a template that can't generate can't
spin the sweep forever. What's missing is the other half: the template stays
`active`, `generated_count` never moves, `last_generated_at` never updates, and
nothing surfaces the skip. Month after month, a subscription invoice that a
tenant believes is being raised into the approval queue simply isn't, and the
only trace is a log line. `GET /api/recurring/{id}/history` shows an empty run
history that is indistinguishable from "nothing due yet".

**Same file, second issue:** `_sweep_tenant` has no per-template guard and a
single commit at the end, so one template that raises aborts that tenant's whole
tick and discards the invoices already generated on it — the identical shape
fixed in `services/vendor_rescreen` (per-item re-read by id + per-item commit +
a counted, logged skip) and in `services/payment_erp_sync`. Fix both together.

**Recommended fix.** Persist the skip rather than only logging it: stamp a
reason on the template (a `meta`/settings-JSON marker needs no migration) and
either pause the template after N consecutive non-generatable periods — the
shape `services/scheduled_reports` already uses (`last_run_status` /
`last_run_error` / auto-disable at 5 consecutive failures) — or raise it into
the notification path AP managers already read. Then apply the per-template
guard + per-template commit.

---

## ~~A tenant with no ERP configured never gets an invoice to `paid`~~ — FIXED 2026-08-16

**Resolved** by narrowing the gate to what it actually guards.
`services/payment_erp_sync._sync_payments` no longer returns early on an absent
`settings.erp`; it carries `erp_config=None` into the leg loop, and
`_sync_one_leg` resolves the ERP adapter (and would perform the push) only when
a config exists. Every other per-leg guard is unchanged and shared by both
paths — payment `completed`, invoice `payment_scheduled`, settlement covering,
invoice taken `FOR UPDATE` — so an ERP-less tenant's settled invoices now reach
`paid` while an in-flight payment is still skipped and a *named but unsupported*
ERP type still fails its own leg into the de-duped `erp_reconciliation`
exception (the recoverability semantics of [decisions.md §22](decisions.md) are
untouched).

`get_erp_adapter({})` is deliberately not called on the no-ERP path: it fails
closed on an unusable config ([decisions.md §29](decisions.md)), which would
turn "this tenant has no ERP" into a permanent strand plus an exception row for
a situation that is not an error.

Kept as a stub because the *blast radius* is worth not re-deriving: this module
is the only automatic writer of `payment_scheduled → paid` (the other two are
the manual `POST /api/payments/{id}/settlement/accept` and `api/erp_webhook`,
which by definition requires an ERP), so the early return left every settled
invoice of a "direct schedule, no ERP" tenant at `payment_scheduled` forever —
under-counting the aging report, the `/dashboard` pipeline, the vendor's payment
history and the 1099 YTD totals, and never letting `retention_sweep` see the
invoice as archivable. The payment row stayed correct throughout, which is what
made it invisible from the payments page.

Regression coverage:
`backend/tests/test_payment_erp_sync.py::test_no_erp_configured_still_advances_the_settled_invoice`
(asserts the adapter is never resolved AND the invoice advances) plus
`::test_no_erp_configured_does_not_strand_an_unsettled_invoice` (the no-ERP path
reuses the same guards rather than a looser branch). Contract documented in
`backend/docs/payments.md` § ERP Payment Sync → No ERP configured skips the
push, not the transition.

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
