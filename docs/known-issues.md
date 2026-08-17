# Known Issues

Tracked, non-trivial defects that are diagnosed but not yet fixed. Each entry
names the root cause, the evidence, blast radius, and a recommended fix
approach — this is a staging area for real problems, not a place to let them
go stale. See root `CLAUDE.md` guard rail 6 (no dangling deferred findings).

**One entry is open** (the over-range settlement amount, below). Everything
after it is a `~~struck-through~~` resolved stub, kept because the *diagnosis*
is the expensive part and is worth not re-deriving. Add a new entry above them
when a defect is diagnosed but can't be fixed in the same session.

**Scope:** *diagnosed defects* only. A deferral that isn't a defect — blocked on
a credential, an operator step on merged code, or sized-but-unstarted work —
goes to [followups.md](followups.md). Reasoning behind a deliberate design call
goes to [decisions.md](decisions.md).

---

## An over-range settlement amount wedges the payment webhook instead of flagging it — OPEN, diagnosed 2026-08-17

**Found while** bounding the bulk-intake parsers (`services/numeric_bounds`).
That work covered the three CSV/document parsers; this is the same defect class
on a fourth parser, in the money path, and it does **not** have the same fix.

**Root cause.** `services/payment_adapters/base.py::parse_amount` returns an
unbounded `Decimal` — its only guard is `.quantize(Decimal("0.01"))`, which
raises (→ `None`) for genuinely absurd magnitudes but happily returns a
20-integer-digit value. `payments.settled_amount` is `Numeric(15, 2)`, i.e. 13
integer digits. So a processor webhook reporting an amount between roughly
10^13 and 10^26 parses, verifies, and then raises `NumericValueOutOfRangeError`
at the flush.

**Blast radius.** The wrong thing fails, in the wrong direction. `verify_settlement`
already classifies such a figure correctly — it is nowhere near any authorized
leg, so the verdict is `amount_mismatch`, which is exactly the divergence that
should open a payment-blocking `fraud_flag` and suppress the discount capture.
But the persist at `api/payments.py` (`payment.settled_amount = …`) is inside
the same transaction, so the flush rolls the whole handler back: the fraud flag
never lands, the completion is never recorded, the handler 5xxs, and the
provider retries into the identical failure forever. The single most suspicious
settlement a rail can report is the one the system records nothing about.
`services/payment_reconciler.py` writes the same column from
`fetch_settlement` and has the same exposure.

Reachability is gated by the HMAC — this needs a compromised or buggy
processor, not an anonymous attacker. That is what keeps it a defect rather
than a vulnerability.

**Why it was not fixed alongside the bulk-intake parsers.** The obvious
symmetry is wrong. Returning `None` from `parse_amount` (what the three CSV
parsers do) means "the rail reported no amount", which `verify_settlement`
reads as `unverified` and `settlement_coverage` deliberately fails OPEN on — so
a garbage figure would be laundered into a silent pass and the invoice marked
`paid`. Guarding only the persist and leaving the column NULL lands in the same
place for the same reason. Clamping fabricates a number. A correct fix has to
let coverage distinguish "no figure on record" from "a figure we could not
store", which is a new column or flag — a migration in the money path, with the
review that implies. Out of scope for a commit about CSV parsers.

**Durable fix.** Carry "reported but unstorable" as its own state rather than
collapsing it into NULL. Cheapest shape that preserves the evidence: keep
`verify_settlement`'s verdict and its audit row (JSONB, unbounded — it can
record the reported figure verbatim), persist `settled_amount` only when
`numeric_bounds.fits_numeric(value, 15, 2)`, and add a boolean/flag the
`settlement_coverage` check reads as **not** covered so the invoice holds at
`payment_scheduled` behind the existing accept/void exits. Widening the column
instead is not the fix — it moves the cliff without changing the semantics, and
no legitimate settlement is 14 digits.

**Trigger.** Do it with the next change that already touches
`payment_settlement` / `settlement_coverage`, or sooner if a real processor is
onboarded (the adapter-specific `parse_amount` call sites are `checkeeper`,
`dwolla`, and `mock`). Guard rail 3 applies — money path, so it gets a review
pass.

---

## ~~A non-generatable recurring template silently skips every period~~ — FIXED 2026-08-16

**Resolved**, both halves, in `services/recurring_invoices.py`.

*The silent skip.* The defensive cursor advance was always right — a template
that can't generate must not spin the sweep forever — so what was added is the
missing other half. A skip now stamps a PII-free marker on
`RecurringInvoiceTemplate.meta.generation_skip` (`record_generation_skip` —
reason code, period, consecutive count, timestamp; settings-JSON, **no
migration**), writes a `recurring_template.generation_skipped` audit row
correlated on the template's own id, and rides a `last_skip` field on every
`/api/recurring` response, rendered as a *Not generating* badge on the
`/recurring` list. Past `MAX_CONSECUTIVE_SKIPS` (3) the sweep **pauses** the
template and audits that too (`recurring_template.paused`, `actor_id` NULL,
`source: "sweep"`) — the `services/scheduled_reports` auto-disable shape — so an
unfixable schedule stops claiming to be live. The marker is cleared by **every**
path that establishes the reason no longer holds — `generate_one`, the sweep's
already-generated no-op, `generate-now`'s idempotent branch, and (via
`clear_generation_skip_if_resolved`) `PATCH` and `resume` — which is what makes
`consecutive` mean consecutive; clearing only in the sweep left a manually-fixed
template with a stale count, so its next single miss tripped the three-miss
auto-pause. The "can it generate" verdict itself moved into one pure
`not_generatable_reason`, shared by `generate_one`, the sweep and the router's
`generate-now` 422, so the three can't drift.

The skip count is deliberately **not** a `*_failures` field on `SweepResult`:
`sweep_health.failure_count` sums those, and a template missing a vendor is a
tenant configuration problem, not a broken sweep — counting it would leave the
sweep permanently `degraded` for something no platform operator can fix. The
auto-pause is what bounds it instead.

*The per-tenant commit.* `_sweep_tenant` now selects template **ids**, then
re-reads, guards and commits each template on its own — the `vendor_rescreen`
shape. A template whose generation raises is rolled back, logged by exception
CLASS only, and counted as `template_failures` (which *does* feed
`sweep_health`), while its siblings keep the invoices they already generated.

Regression coverage in `backend/tests/test_recurring_invoices.py`: the persisted
marker + audit row, the auto-pause after N periods (and that a paused template
is left alone), the marker clearing once the template generates, the
sibling-survives-a-poison-template proof, the PII-out-of-logs assertion, and the
API's `last_skip` (present / absent / malformed-`meta` tolerated). Frontend map
guarded by `frontend/src/lib/types/recurring.test.ts`. Documented in
`backend/docs/recurring-invoices.md` § A skipped period is never silent and
§ One template's failure never costs its siblings their work.

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
