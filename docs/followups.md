# Open follow-ups

**Open items only.** Completed items are pruned as they land — the code is in
git history and the reasoning belongs in [decisions.md](decisions.md). This file
should shrink as often as it grows.

Every item here is one of:

- **(a)** blocked on an external credential, account, vendor engagement, or
  hardware we don't have;
- **(b)** an operator step on code that is already merged;
- **(c)** a sized-but-unstarted piece of work, or a deferred-with-reason finding
  awaiting a product or architecture call.

This is the destination root `CLAUDE.md` guard rail 6 demands for a deferral —
"deferred / recommended" in a report is a staging area, not an end state. An
item lands here **with its category, the durable fix, and the trigger to do it**,
or it doesn't get deferred.

**What does not belong here:**

| That | Goes here |
|---|---|
| Diagnosed defects with a root cause | [known-issues.md](known-issues.md) |
| Scope + status of work still open | [roadmap.md](roadmap.md) |
| Scope of work already shipped | [roadmap_shipped.md](roadmap_shipped.md) |
| Why something was built the way it was | [decisions.md](decisions.md) |

Each open roadmap section carries an `**Open:**` line naming what's left; the
matching entry here carries the category, durable fix, and trigger. Keep the
pair consistent — if an item leaves this file, its roadmap section either loses
its `**Open:**` line or moves to the archive.

Mirrored as GitHub issue [#251](https://github.com/Absence0760/project-account-payables/issues/251)
for the tracker view. Keep the two reconciled when either moves.

**Last reconciled:** 2026-08-10 against `9d9daabb`. Closed since the prior pass:
the AI Cash-Flow Copilot Phase 3 core (draft-run / capture-discounts / enact
affordance — shipped in #258, only its already-deferred sub-bucket remains
below), the i18n date-localization slice (shipped in #258 — verified zero
remaining `toLocaleDateString` call sites outside `utils/time.ts`), all three
in-source TODOs (verified removed from source), and all three diagnosed
defects in [known-issues.md](known-issues.md) (all now fixed/resolved — the
"Diagnosed defects awaiting a fix" section is retired until something new
lands there).

---

## (c) Feature work — sized and unstarted

### AI Cash-Flow Copilot — Phase 3 deferred bucket

Phases 1–3 core shipped (read-only cash Q&A, `propose_payment_plan` +
`PlanCard`, and draft-run/capture-discounts enactment — see
[roadmap.md](roadmap.md) § AI Cash-Flow Copilot). Only the
originally-deferred sub-bucket from that same feature remains:

- [ ] Saved plans / plan-vs-actual (`CashPlan` model + migration)
- [ ] Opening-balance provenance surfacing
- [ ] Consolidated cross-entity mode
- [ ] Proactive shortfall-alert sweep

**Trigger:** next feature slice. Nothing blocks it.
Refs: [roadmap.md](roadmap.md) § AI Cash-Flow Copilot,
[cash-flow-copilot.md](cash-flow-copilot.md).

### Vendor statement reconciliation — PDF intake

CSV upload and the manual pasted-lines path both ship. A supplier statement that
arrives as a PDF still has to be transcribed by hand.

- [ ] PDF-via-extraction statement intake + raw-file storage — route the PDF
      through the existing extraction pipeline rather than adding a second
      parser.

**Why deferred:** the CSV/manual paths cover the common case, and reusing the
extraction adapters properly is a real slice rather than a bolt-on.
**Trigger:** a pilot tenant whose suppliers send PDF statements.
Ref: [vendor-statement-reconciliation.md](../backend/docs/vendor-statement-reconciliation.md) § Deferred.

### Mount-time double-fetch race — invoices/vendors' local-mutation bypass

`frontend/src/lib/components/admin/UsersPanel.svelte` had two `$effect`s that
both called `adminStore.fetchUsers()` on mount — the search-debounce effect
fired an unguarded duplicate ~250ms after the immediate one (a Svelte
`$effect` always runs once on mount regardless of whether its tracked value
changed). Because the store always *replaces* the list wholesale, whichever
of the two fetches resolved last could silently clobber an optimistic
create/delete with a stale snapshot — a real, user-visible race, not a test
flake (root-caused and fixed via `/flake-doctor`, PR #286).

An independently-verified `/bug-hunt` sibling sweep (each page's actual
`$effect` blocks read, not just grepped) found the identical pattern —
unguarded duplicate mount fetch + a local-only mutation splice with no
sequencer — on five more pages, now fixed the same way (a guard skipping
the second effect's own mount-time run): **`budgets`, `contracts`, `intake`,
`recurring`, and `purchase-orders`** (the last caught in code review, missed
by the initial sweep — narrower blast radius since it's read-only/ERP-synced
with no local-splice mutation, but `syncFromErp()`/`loadMore()` could still
be overwritten by the delayed duplicate). Five other pages were checked and
confirmed NOT at risk: `catalogs` and `expenses` only ever have one
fetch-triggering effect; `payments`, `positive-pay`, `requisitions`,
`vendor-statements` each have a request sequencer and/or route every
mutation through a full sequencer-protected refetch, closing the race from
a different angle.

**Still open — a narrower variant on `invoices` and `vendors`:** both pages
already carry a request sequencer (`createRequestSequencer()`) that correctly
resolves fetch-vs-fetch ordering, so the `UsersPanel`-style guard doesn't
apply to them. But each has **local-mutation helpers that bypass the
sequencer entirely**: `invoiceStore.update()` / `patchLocal()` (used by
`InvoiceModal`'s save/approve/reject/file-attach) and `vendors/+page.svelte`'s
`applyVendorUpdate()` (bank-detail edit, screening, risk-recompute,
block/unblock) mutate the list directly without calling
`fetchSequence.start()`. A still-in-flight mount-time fetch — the sequencer
only drops it if a *newer sequenced fetch* supersedes it, which these local
mutations never trigger — can resolve after one of these edits and overwrite
it with a stale pre-edit snapshot.

- [ ] Route `invoiceStore.update()`/`patchLocal()` and
      `vendors/+page.svelte`'s `applyVendorUpdate()` through the same
      sequencer their pages already use (mark the local mutation as
      superseding any in-flight fetch, or have it call
      `fetchSequence.start()`/mark-latest before applying), so a stale
      redundant fetch can never clobber a local edit either.

**Why deferred:** this is a different code shape from the four just fixed
(threading state through an existing sequencer rather than adding a mount
guard) and touches two higher-traffic pages — worth its own focused pass
rather than folding into the mechanical sibling-sweep fix.
**Trigger:** next `/flake-doctor` or `/bug-hunt` pass touching `invoices` or
`vendors`, or a bug report matching this symptom on either page.
Ref: `reviews/flake-admin-users.md` (gitignored — regenerate via
`/flake-doctor` if consulting this again after the file has aged out).


### `payment_compliance_hold` resolution bypasses the exception chokepoint

`backend/app/api/payments.py::_resolve_compliance_hold_exception` carries its
own copy of the exception-resolution bookkeeping (status / resolution /
resolved_by / resolved_at / time_to_resolution) and writes **no**
`exception.resolved` audit row. Its docstring says it is "duplicated rather than
cross-imported from that router module" — which was correct when the only other
copy lived in a router, but no longer is: the bookkeeping + its append-only
audit row now live in the service `app/services/exception_lifecycle.py`, and
both the human queue (`api/exceptions`) and the agent coordinator go through
`record_decision`. Consequence: releasing or dismissing a sanctions/KYC hold
flips the exception with no immutable trace of *that* flip (the payment action
itself IS separately audited), and the third copy can drift from the other two —
it already would have missed the escalate-is-not-a-resolution correction.

**Durable fix:** call
`services/exception_lifecycle.record_decision(db, exception=exc, action="resolve",
resolution=…, actor_id=user.id, actor_name=user.full_name)` and delete the local
mutator. Two call sites (`/compliance/release`, `/compliance/dismiss`); no
schema change; covered by extending `backend/tests/test_exception_audit_trail.py`.
**Trigger:** next change that touches the compliance-hold path — it was left out
of the exception-audit round only because `api/payments.py` was owned by a
concurrent session at the time. *(c) sized, unstarted.*

---

## (a) Blocked on external credentials, accounts, or hardware

None of these are startable from the editor. They are listed so they don't read
as oversights.

- [ ] **SOC 2** — vendor selection (Vanta / Drata / Secureframe / Sprinto),
      policy library, onboarding/offboarding checklist with evidence collection,
      incident-response runbook + on-call rotation, Type I audit, then the Type II
      observation window. **All engineering prereqs are complete**; this is
      process work behind a founder decision and a vendor contract.
      Ref: [soc2-readiness.md](soc2-readiness.md).
- [ ] **Live government e-invoice clearance** — SdI (IT), SAT-PAC (MX), SEFAZ
      (BR), DIAN (CO). The generators and national validation ship as pure
      local-first code; only live authorization remains, and each needs its own
      country registration. Ref: [peppol.md](../backend/docs/peppol.md).
- [ ] **Live sanctions-provider wiring** — the ComplyAdvantage / Dow Jones /
      Refinitiv adapters are fail-closed skeletons awaiting keys. `mock` is the
      local-first default and the screening path itself is shipped and tested.
      Ref: [vendor-risk-screening.md](../backend/docs/vendor-risk-screening.md).
- [ ] **Stripe Billing** — a provisioned Stripe account to verify the live
      `stripe_billing` adapter path end-to-end. All the code that needs it is
      shipped, including the plan-change UI (`/billing`, tested against the
      `mock` adapter) — this is purely the credential to validate the real
      Stripe leg.
      Ref: [billing.md](../backend/docs/billing.md).
- [ ] **Mobile push (FCM + APNs)** — a Firebase project,
      `google-services.json` / `GoogleService-Info.plist`, and an APNs auth key.
      Device-token registration + notification-tap deep-linking are shipped
      (`push_service.dart`); what's blocked is the push-*sending* adapter
      itself, which needs these credentials to build against.
- [ ] **Manual screen-reader device pass** — VoiceOver / NVDA / TalkBack. The
      procedure is documented and repeatable; it needs real AT hardware, so it
      cannot run in CI. The automated axe-core + `meetsGuideline` guards ship.
      Ref: [accessibility-screen-reader-checklist.md](accessibility-screen-reader-checklist.md).
- [ ] **Banking-aggregator (Plaid-style) balance feed** — the bring-your-own and
      provider-`get_balance` paths ship; a real aggregator needs an account.

---

## (b) Operator steps on merged code

- [ ] **TLS/DNS provisioning runbook for a partner-provisioned child tenant's
      vanity domain.** `POST /api/partner/children/provision` and the
      custom-domain resolver both ship ([decisions §14](decisions.md)); what's
      missing is the written operator procedure for issuing the certificate and
      pointing DNS at the new tenant's hostname.
      **Durable fix:** a runbook under `docs/founder-runbooks/`.
      Ref: [white-label.md](white-label.md) § Custom domains.

- [ ] **OPTIONAL — `DEPENDABOT_LOCKFILE_PAT` is unset, so lockfile sync is
      inert.** `.github/workflows/dependabot-lockfile.yml` regenerates the
      frontend pnpm lockfile and the backend `requirements{,-dev}.lock`, then
      pushes the result onto the Dependabot branch. Its `push` job gates on
      that PAT and skips with a log line when unset — and
      `gh api repos/:owner/:repo/dependabot/secrets` reports `total_count: 0`,
      so the gate has never opened (the pnpm half has never fired either).
      Consequence: a Dependabot manifest bump arrives red, because the stale
      lock fails `backend/tests/test_dependency_lock_sync.py`, and the locks
      get regenerated by hand instead (commands in
      [backend/CLAUDE.md](../backend/CLAUDE.md) § Dependency lock — roughly two
      minutes, weekly at most).

      **This is a convenience, not a blocker, and deliberately not scheduled.**
      Nothing is unsafe while it sits unset: the guard test fails loudly rather
      than shipping undeclared pins, which is the behaviour we want either way.
      It is also worth being honest about the value on offer — Dependabot bumps
      *floors* in `pyproject.toml`, and the floor is not what ships; the lock
      is. The one genuine vulnerability this area has produced so far
      (CVE-2026-69247, `cryptography` 49.0.0) was invisible to Dependabot
      because `cryptography` is transitive via `python-jose[cryptography]` and
      never named in the manifest. Trivy caught it by scanning the built image.

      **Higher-value neighbour:** Dependabot *alerts* are disabled repo-wide
      (`GET /dependabot/alerts` → 403). Enabling them is a settings toggle
      needing no credential, and it is the check that would have flagged the
      `cryptography` CVE against the dependency graph rather than leaving it to
      a container scan. Prefer that over this entry.

      **Durable fix (when wanted):** a fine-grained PAT scoped to this repo
      with `Contents: Write`, stored in the **Dependabot** secret store — NOT
      the Actions store, since Dependabot-authored PRs cannot read Actions
      secrets: `gh secret set DEPENDABOT_LOCKFILE_PAT --app dependabot`. A
      GitHub App token via `actions/create-github-app-token` is the sturdier
      variant — no expiry, not bound to one person's account. The workflow
      header documents the full rationale.
      **Trigger:** when regenerating locks by hand becomes a recurring
      irritation — not on any fixed schedule.

---

## (c bis) Trust-boundary round — deferred

### `tenantSlugUsage` vitest guard is red on `main`

`frontend/src/lib/tenantSlugUsage.test.ts` asserts `InvoiceModal.svelte`
contains `import { getTenantSlug }`. The modal no longer derives a tenant slug
at all (it goes through `$lib/api`), so the guard fails on a file that is
*more* correct than when the guard was written — a stale source-scan, not an
app defect. **Durable fix:** narrow the guard to the sites that still build
headers by hand, or drop the site from `SITES` if none remain.
**Trigger:** next change touching `InvoiceModal.svelte` or the frontend unit
suite (it is the only red test in `pnpm test:unit`).

### Playwright cover for the `/profile` Signed-in devices panel

Backend session visibility + revocation is covered by pytest
(`tests/test_auth_sessions.py`, `tests/test_session_management.py`); the UI
panel has no e2e. The natural spec — sign in twice, revoke-others from the
second, confirm the first token bounces to `/login` — needs the full dev stack
on the shared `:7777`/`:8000` ports, which was not safe to claim during a
multi-session round. **Durable fix:** add
`frontend/tests-e2e/auth/sessions.spec.ts` alongside `signout.spec.ts` (same
fresh-sign-in `storageState` treatment — revocation invalidates the worker's
cached JWT). **Trigger:** next e2e pass with the stack already up.

---

## Money path — surfaced by an `/improve-round` survey, not yet fixed

Each was verified by reading the code; none is speculative. Grouped here so
they are picked up as a batch rather than rediscovered one at a time.

- [ ] **Entity scope missing on every payment detail/mutation route.**
      `api/payments.py` scopes list / queue / summary / counts / runs-list /
      run-create by `X-Entity-ID`, but `get_payment`, `void_payment`, the two
      compliance handlers, `get_payment_run`, `approve`, `cancel`, `execute`
      and `resume` are all unscoped — so a user with entity A selected can act
      on entity B's money by id. Same tenant, different subsidiary. **Durable
      fix:** a `_get_scoped_*` helper applying `apply_entity_scope`, mirroring
      `api/positive_pay.py::_get_scoped_file` which already does this on the
      sibling treasury router. **Trigger:** next round touching
      `api/payments.py`; treat as the highest-priority item here.
- [ ] **`pending_compliance` is a dead end in the UI.** The status is absent
      from `frontend/src/lib/types/payment.ts`, so it has no chip, no filter
      and renders as a blank badge; `canVoid()` excludes it; and the two
      purpose-built exits (`POST /payments/{id}/compliance/{release,dismiss}`)
      have zero frontend callers. Meanwhile the invoice stays `approved` and
      re-enters the queue, but `uq_payments_one_live_per_invoice` rejects a new
      run for it with a message naming no invoice. **Durable fix:** add the
      status to the frontend enum + chips, wire the two endpoints into the
      payment row's actions, and name the offending invoices in the 409.
- [ ] **A payment run doesn't show why anything failed, and there is no
      retry.** `failure_reason` exists on the model and is populated on every
      failure path, but is absent from `PaymentResponse` and the run-detail
      payload; the partial-failure counts are only a transient toast; `partial`
      / `executing` / `cancelled` are missing from `PaymentRunStatus`.
      **Durable fix:** add `failure_reason` (+ `submitted_at` /
      `completed_at` / `provider`) to the read surface, persist the run rollup,
      complete the status enum, and add a per-run "retry the failed ones".
- [ ] **`POST /api/payments` (standalone) ignores applied credit memos.**
      `services/payment_runs.py` nets them (`inv.amount - already_applied`);
      the standalone endpoint pays `invoice.amount` and 422s any other amount,
      so the correct net figure cannot be submitted. Currently unreachable (no
      client calls it) but documented in `api-reference.md`. **Durable fix:**
      route it through the same netting helper.
- [ ] **Positive Pay check-issue file can be generated for a *draft* run.**
      `generate_check_issue` never checks `run.status`; on a draft every
      payment is `pending` with `reference IS NULL`, so `issued_map` persists
      empty — and the endpoint is idempotent per `(run, bank_format)`, so it
      can't be regenerated after execution. `POST /{id}/process-return` then
      classifies every presented cheque `not_on_file` → a flood of false
      `fraud_flag` exceptions on real payments. **Durable fix:** 422 when
      `run.executed_at is None`, and filter the picker in
      `PositivePayModal.svelte` to executed runs.
- [ ] **Bank-statement matcher ignores payment status and currency.** A bank
      debit can auto-match at confidence 100 to a payment our books call
      `failed`, `voided` or `pending` — the discrepancy reconciliation exists
      to surface, converted into a match. `BankTransaction.currency` is never
      compared either, so a €1,000 debit matches a $1,000 payment. **Durable
      fix:** filter candidates by status and compare currency, mirroring the
      `amount_mismatch` classification rather than silently refusing to match.
- [ ] **`POST /api/bank-reconciliation/upload` has no idempotency and no size
      cap.** No content hash (contrast `PositivePayFile.content_hash`), no
      `(org, account, period)` uniqueness, and `await file.read()` buffers the
      whole upload. A double-click creates a duplicate statement that reports
      `matched_count = 0` (every payment is already claimed), which reads as
      "nothing reconciled" rather than "you imported this twice". **Durable
      fix:** hash the body, dedupe on `(org, account_identifier, hash)`, and
      cap the read.
- [ ] **No unique index behind "one payment, one bank transaction".**
      `/resolve` now row-locks the payment and the matcher keeps a `claimed`
      set, but two concurrent `/upload`s can still both claim one payment, and
      existing data may already hold duplicates. **Durable fix:** a partial
      unique index on `bank_transactions.matched_payment_id WHERE
      matched_payment_id IS NOT NULL`, in the same shape as
      `uq_payments_one_live_per_invoice`. Needs a migration *and* a decision on
      what to do with any pre-existing duplicates, which is why it isn't folded
      into the lock fix. **Trigger:** next `/safe-migration` pass on the tenant
      schema.

---
