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

**Last reconciled:** 2026-08-18 against the money-path pass of round 10 —
seven defects fixed at the root, each reproduced with a failing test first (see
§ Surfaced by the money-path bug hunt (round 10) for the remainder).

Before that: 2026-08-17 against `fix/bug-hunt-round-9` — a five-agent
bug hunt that confirmed ~50 findings and fixed 31 at the root (see § Surfaced by
the five-agent bug hunt for the remainder, which is the largest single addition
this file has taken).

Before that, against round 8 (`feat: round 8 — exact money serialization,
bulk-intake bounds, and the dependency backlog`, #312) plus one same-day
follow-up. Round 8 closed every `(c)` entry that had been sitting in
§ "Surfaced while closing the above, deliberately not fixed" — money is now
exact throughout `api/analytics.py` (the `float()` calls remaining there are
`dpo`/`*_pct` and day-count fields, never money), every expense `Numeric` field
is digit/scale-bounded, `chat_notifications.webhook_url` has an audited rotation
endpoint, and `notification_dispatch._send_chat_best_effort` no longer names
the invoice id `entity_id`. The follow-up closed the section's last two: a
hint on the `/cfo` DPO trend chart naming its own closed-months window, and
`frontend/tests-e2e/organization/data-residency.spec.ts` (the Data Residency
panel's missing e2e coverage, modeled on the sibling Custom Domains spec).
That empties the section, so it's removed rather than kept as a "— CLOSED"
stub.

Before that: 2026-08-16 against `improve/round-batch-3` — a three-agent
round that closed **every remaining actionable `(c)` item**. What is left in
this file is the `(a)` credential-blocked set, the `(b)` operator steps, and two
`(c)` entries that are product calls rather than work (an unwired adapter family
and the copilot's saved-plans bucket), plus the badge-spelling consistency
sweep.

A later backend round added one more `(c)` entry — eight built-and-documented
capabilities with no production caller — found while closing the
adapter-registry defect behind [decisions §29](decisions.md). That round did
close the sharp half of the same survey: all three money-touching dispatchers
(payments, ERP, FX) resolved an unrecognised provider name to their **fixture**
adapter, which is not an inert stub — `mock.create_payment` reports every
payment settled, `mock.parse_webhook` verifies no signature, `mock.post_invoice`
returns a fabricated ERP document id, and the mock FX rate got *locked onto the
Payment row* — so one typo in an admin-entered settings value silently produced
paid-but-unpaid invoices, an unverified public webhook parser, ERP references
pointing at nothing, and a permanently mis-priced outflow. All three now fail
closed, with each caller deciding what the refusal means.

**The vendor-statement upload UI closed** — but the entry's premise ("no file
picker at all") was stale; a picker existed and already took CSV and PDF. The
real defects were sharper: the two intakes competed silently (a file beat typed
lines, and `notes` was dropped on the upload path the endpoint doesn't accept it
on), the backend's PII-free 422 refusals — the actionable half of a reader that
*skips rather than guesses* — went to a fading toast, `has_source_file` /
`extraction` were never typed client-side, and an empty pasted editor created a
run asserting the supplier had listed nothing. Intake is now an explicit mode
choice, refusals render as a persistent inline alert, and the run detail carries
a source pill, the `extraction` provenance block and a download of the archived
supplier document. That was the last open item in the Vendor Statement
Reconciliation roadmap section, which moved to
[roadmap_shipped.md](roadmap_shipped.md). It surfaced two new items — the
keyless-dev-box extraction fallback, and the reader's uncounted skipped rows —
**both of which are now closed too** (see the extraction paragraph below).

**The invoices/vendors local-mutation race closed** — the fix went into the
shared `createRequestSequencer()` primitive rather than being hand-rolled per
page, splitting the old single `isLatest` predicate into `canCommit` (may this
response be written?) and `isCurrentRequest` (is this still the newest request?)
so a `finally` clearing a loading flag doesn't hang forever once a local edit
supersedes an in-flight fetch. Rationale in [decisions.md](decisions.md) §23.
The exhaustive sweep it prompted found the far larger remainder — eighteen list
surfaces with no sequencing at all — and a separate `/assistant` defect.

**Both of those are now closed too**, in the frontend round that had the whole
app to itself. Every list store and list route named in that sweep is on the
shared primitive, `InvoiceModal`'s line-item editor and the `workflows/[id]`
canvas with it (an editor over a fetched list loses unsaved work, not just a
row), and the issue-#168 `untrack` fix reached the eight filter pages that
never got it — `syncUrl()` is untracked wholesale there, since it writes URL
state and is not a dependency source. `/assistant` now holds `busy` for the
whole conversation load *and* resolves its in-flight bubble by identity rather
than by a captured index, so a replaced array can't misdirect the model's
answer onto an unrelated historical message; `/cash-flow`'s copy of the same
code got the identity half so it isn't the version copied from next. The
per-list (not per-file) sequencer rule, the untracked-writer rule and the
identity rule are in [decisions.md](decisions.md) §25; the pattern doc is
`frontend/CLAUDE.md` § Sequencing list fetches. Guards:
`tests-e2e/reactivity/local-edit-vs-inflight-fetch.spec.ts`,
`tests-e2e/assistant/thread-load-race.spec.ts`, and `/recurring` joining the
existing `search-debounce-race.spec.ts` table.

**The ERP sync-back's failures became visible and recoverable** —
`services/payment_erp_sync` is the only path that flips an invoice
`payment_scheduled → paid`, is dispatched one-shot after a terminal event and is
never re-invoked for an already-`completed` payment, so a failed leg left the
money moved and the invoice stranded forever behind a log line. Worse, one
shared transaction meant a leg failing on a DB error rolled back the run's
*successful* transitions too. Each leg now commits independently, every failure
opens a de-duped PII-free `erp_reconciliation` exception, and
`POST /api/payments/runs/{run_id}/sync-erp` is the audited retry exit.
Rationale in [decisions.md](decisions.md) §22. Two sibling fixes rode along (a
fail-open `quote_payment` base default, and a head-of-line stall in
`vendor_rescreen`); the systemic remainder — every sweep discarding its own
failure count — became its own entry, **now closed** (next paragraph).

**Every background sweep's failure count became state.** All fourteen
long-lived sweeps carried a private copy of the same loop body and discarded the
result their `*_once()` returned; twelve of those results already carried a
`failures: int` that nothing read. There was no supervision either
(`asyncio.create_task` with no `add_done_callback`), so a sweep whose loop died
was gone for the life of the process with nothing saying so — an
`audit_shipping` sink misconfigured for months, its SOC 2 WORM evidence never
leaving the tenant DB, looked exactly like one running clean. The fix is the
single mechanism the entry asked for rather than fourteen ad-hoc ones:
`services/sweep_health.py` owns the loop *body* (`run_sweep_loop`), so the
outcome is recorded by construction and the bodies can't drift again. A tick
that completes reporting `failures > 0` counts as failed — modelling only "did
it raise" would have left the motivating case invisible — and a tick that hangs
inside `*_once` is reported `stalled` rather than sitting in `running` looking
healthy. Supervision goes through `start_sweep()`, with an AST scan failing the
suite if a raw `create_task` returns. Admin-gated `GET /api/health/sweeps`
reports it; public `GET /api/health` is unchanged, deliberately, so a
misconfigured sink can't become a rolling restart loop. Rationale in
[decisions.md](decisions.md) §24. It also fixed a real PII leak found along the
way: eight loops passed `exc_info=True` / `logger.exception`, which appends the
whole traceback regardless of the format string — `payment_reconciler` had
diagnosed that in a comment and fixed it for itself alone.

**Platform extraction stopped calling out from a keyless dev box, and the
statement reader started admitting what it refuses.**
`_resolve_extraction_config` hardcoded `claude_vision` for platform mode — the
default for every seeded tenant — *regardless* of whether
`FEOH_ANTHROPIC_API_KEY` was set, so a fresh clone POSTed to `api.anthropic.com`
with an empty key on every extraction, breaking guard rail 7 for the whole
extraction path rather than just statements. The new pure
`resolve_platform_provider` resolves explicit `FEOH_EXTRACTION_PROVIDER` → a
configured key → `claude_vision` (the deployed path, byte-identical) → keyless
and non-deployed → `mock`. A keyless *deployed* env deliberately does **not**
fall back: `mock.extract` returns a fabricated invoice at 0.95 confidence, inside
the band that can auto-approve, so a lost credential would start booking invented
payables against real vendors. An unregistered `FEOH_EXTRACTION_PROVIDER` is
refused at boot, because the dispatcher silently falls back to `mock` on an
unknown name and the new env var would otherwise have made a typo a route to the
fixture adapter. Rationale in [decisions.md](decisions.md) §26. Separately,
`scan_statement_text` now classifies each skip where it happens — "not a row"
(blank lines, headers, page furniture, subtotals) stays silent, "ambiguous"
(a second money column, a second reference column) is counted and surfaced
through `meta.extraction` — which is the honest split the entry said was the
actual design problem: a clean statement reports 0, an aging statement reports
one per data row. Building it surfaced a pre-existing mis-accept, where
`Current: 1,200.00  Past due: 850.00` was booked as an open item with invoice
number `1,200.00`; that is fixed, and the refusal is announced rather than
silent whenever the row has an open item's shape.

**Two reporting gaps closed with them.** `/cfo` now distinguishes "we have a
bank balance but declined to use it" (`opening_balance_provider_skipped`, e.g.
`currency_mismatch`) from "no bank is connected" — the API already carried the
reason and the page rendered only `source === 'none'`, so the two looked
identical on the surface where the number is actually read. And the axe
accessibility guard gained all four `/admin` routes plus `/vendor-statements`
(list and create modal) — the surface carrying dialogs, armed two-click
destructive actions and one-time secret reveals, which is exactly where a
focus-management regression is most costly and where the guard was silent.

**The axe route list stopped trailing the app — by not being the only guard.**
Its entry asked two things: add four more routes, and *consider* whether a
token-pairing lint beats route-by-route coverage for this class. Both are done,
and the second turned out to be the whole answer. Scanning the stylesheets
instead of the rendered routes found **99** problems the route list could never
have covered: 55 colour pairs below 4.5:1 (the `--accent-strong` companion had
existed for a round and almost nothing used it — 40 buttons and chips still
filled with `var(--accent)` at 3.12:1; green and red had no companion at all,
so pay / approve / execute / reject / void all failed), 32
`var(--token, fallback)` declarations whose fallback contradicted its token,
and 12 references to a token nothing ever assigns. A fourth rule, added once
those were green, found the largest single defect of the lot: a bare literal
`color:` renders on whatever the cascade supplies, and `#e04040` — the status
red on error messages, alerts and the danger row-action — is 4.11:1 on
`--surface`, in **106** declarations across 61 files. Twelve more came out
once the rule stopped standing down on a translucent tint, which is the
standard status-pill shape. `--success{,-strong}` and `--danger{,-strong}` now
exist alongside `--accent{,-strong}`, every site is fixed, and
`frontend/src/lib/a11y/tokenPairing.test.ts` fails the suite on a recurrence —
with no suppression mechanism, since a `-strong` companion means a correct
answer always exists. The four routes went in too; the two guards are
complements (the scan can't resolve the cascade, axe can't see a surface no
listed route renders). Rationale in [decisions.md](decisions.md) §28. It also
surfaced the one hole neither can close — white-label theming writes a tenant's
hex straight into `--accent-strong` at runtime — now an inline contrast
advisory on both surfaces that edit it.

Closed in the pass before that, against `improve/round-followup-closeout`: the
three tracked items of that round. **Webhook secret rotation became reachable
from `/admin/webhooks`** (row action → overlap picker → one-time reveal), with
its overlap badge surviving a reload now that `SubscriptionResponse` carries the
expiry. **Vendor statements began accepting a PDF**, routed through the org's own
extraction adapter as an optional `extract_statement` capability rather than a
second parser, with the source document archived. **The cash-flow copilot gained
a proactive shortfall-alert sweep**; its opening-balance-provenance bullet turned
out to be already shipped, and closing it surfaced a real defect underneath — the
provider balance's CURRENCY was dropped, so a USD-reporting org with a EUR
account got a running balance that was silently a two-currency mixture. That is
fixed and the CFO endpoint now shares the same resolution chain.

Closed in the pass before that, against
`feat/settled-amount-and-money-path-followups`: the entire
money-path batch surfaced by the settlement-verification round — **under-settlement
closing the invoice out as fully paid** (migration `0083` puts the settled figure
on the row; the ERP sync holds a short/uncertain settlement and
`POST /api/payments/{id}/settlement/accept` is the release the earlier reverted
attempt lacked), **the two rails settlement verification couldn't reach** (the
optional `PaymentAdapter.fetch_settlement` capability, implemented for Dwolla and
called by both the webhook fallback and the reconciler backstop), **the
minor-unit 2-digit-exponent assumption** (both legs now resolve the real ISO-4217
exponent, moved together), and **`compute_fx_gain_loss` as documented behaviour
over an unwired function** (given a production caller on the settlement audit row,
after removing the dead parameter and the inverted rate convention that made it
unwireable). Only the trust-boundary item below is carried forward.

Closed in the pass before that: the whole **Money path** batch (all eight items — entity
scope on every by-id payment/run route, the `pending_compliance` UI dead end,
run failure visibility + retry, credit-memo netting on the standalone path,
the draft-run Positive Pay guard, bank matching on status + currency, statement
upload idempotency + size cap, and the one-payment-one-bank-transaction unique
index in migration `0081`) and the whole **Trust-boundary** batch (the stale
`tenantSlugUsage` guard, and Playwright cover for the `/profile` Signed-in
devices panel). Nothing from either was carried forward.

Closed in the pass before that: the AI Cash-Flow Copilot Phase 3 core
(draft-run / capture-discounts / enact affordance — shipped in #258, only its
already-deferred sub-bucket remains below), the i18n date-localization slice
(shipped in #258 — verified zero remaining `toLocaleDateString` call sites
outside `utils/time.ts`), all three in-source TODOs (verified removed from
source), and all three diagnosed defects in
[known-issues.md](known-issues.md) (all now fixed/resolved — the "Diagnosed
defects awaiting a fix" section is retired until something new lands there).

---

## (c) Feature work — sized and unstarted

### Two adapter families ship code no caller reaches

Both are latent traps rather than live defects — nothing calls them today — but
each would misbehave for whoever wires it up first:

- [ ] **`services/corridor_quotes.compare_quotes` has no production caller.**
      The multi-provider price optimizer is fully built and documented
      (`backend/docs/international-payments.md` § Multi-route quote
      optimization) but `grep` finds no call site outside its own module.
      Its base-class fail-open bug — an adapter with no fee schedule winning
      every auction with a fabricated free/instant quote — was fixed this round
      (`PaymentAdapter.quote_payment` now returns `no_quote_endpoint`), so
      wiring it up is now safe; what's missing is the wiring, plus
      `modern_treasury`'s real fee table so it isn't skipped.
- [ ] **`services/financing_adapters` has no caller, and `c2fo.py` breaks its
      own Protocol.** `base.py`'s contract says an implementation returns an
      ineligible `FinancingQuote` rather than raising; `C2FOAdapter.quote` and
      `.request_funding` both `raise NotImplementedError`. The `mock` sibling
      returns real quotes. Unreachable today, so it fails no test — and it will
      surface as a 500 for the first caller instead of the documented graceful
      "not eligible".

**Why deferred:** both are wiring/product decisions (where in the payment flow a
corridor auction runs; whether supply-chain financing is offered at all), not
defects in shipped behaviour.
**Trigger:** the first slice that consumes either family.
Ref: `backend/docs/international-payments.md`,
`backend/docs/dynamic-discounting.md`.

### Backend capabilities with no production caller — CLOSED

All eight entries that stood here were landed in one five-agent round (see
`git log --oneline` for `round7/*`). Each was a built, tested, documented
capability that nothing in `app/`, `scripts/` or `alembic/` reached; three of
them turned out to be masking a live defect rather than merely being unwired:

- `analytics.compute_dpo_trend` — the two inline copies had already **diverged**
  (one excluded `rejected` invoices from the COGS proxy, the other didn't), so
  `/api/analytics/drill/dpo` reported 3.0 days where the chart it explains
  showed 30.0.
- `workflow_engine.is_known_step_type` — `POST /api/workflows/import` is the one
  save path a Pydantic `Literal` doesn't constrain, so a typo'd `"aproval"`
  persisted and was silently skipped at runtime, which the engine reads as *no
  approval step configured*. A spelling mistake could drop a financial control.
- `international_payments.is_international_payment` — unifying the three
  hand-rolled rail sets exposed that a per-org `high_risk_corridor_methods`
  entry of `"SEPA"` (or a blank `[""]`) made `_kyc_required_for` fail **open**,
  disabling the KYC gate for that corridor — or, for a blank entry, for every
  corridor.

The remaining five (`expense_policy.mileage_reimbursement`, Teams outbound
approval actions, sanctions `ScreeningResult.categories`,
`data_residency.check_residency_alignment`, the `avalara`/`taxjar` skeleton
probes) were wiring gaps as described, and are now wired, tested and documented.

Rationale for the non-obvious calls made while closing them:
[decisions.md](decisions.md) §31–§34.

### Surfaced by the five-agent bug hunt, deliberately not fixed

The hunt (branch `fix/bug-hunt-round-9`) confirmed ~50 findings and fixed 31
at the root. These are the remainder — each real and reproduced, each larger
than a bug fix or a product call rather than a defect:

- [ ] **Every supplier-portal list truncates at 20 rows with no pager.**
      `frontend/src/routes/portal/{invoices,payments,purchase-orders,discount-offers}/+page.svelte`
      each fetch the bare URL, read only `res.items`, declare `total` and never
      read it, and render no pagination control — while all four routers
      (`backend/app/api/portal.py`) return `{items, total, page, page_size}`
      with `DEFAULT_PAGE_SIZE = 20`. A supplier with 25 invoices sees 20 and no
      count; invoices 21+ and their chat threads are unreachable, older
      remittances and PO flips likewise, and discount offers past the first 20
      expire un-actionable. Raising `page_size` only moves the cliff to
      `MAX_PAGE_SIZE`.
      **Durable fix:** thread `?page=&page_size=` through `portalApi`, keep
      `total` in page state, and reuse the AP app's Load-More control — four
      routes, one shared pattern.
      **Trigger:** the next slice touching the supplier portal's UI.
- [ ] **Non-admins cannot submit an invoice when the approval step uses
      `approver_strategy: "manual"`.** `InvoiceModal.svelte` sources the
      approver picker from `GET /api/admin/users`, which is
      `require_roles(ROLE_ADMIN)`. For every non-admin the call 403s,
      `adminStore.users` stays `[]` with no catch, the `<select>` renders only
      its placeholder, and Submit stays permanently disabled. On the seeded demo
      tenant the workflow's approval step IS `manual`, so `demo+apclerk@acme.com`
      — the role whose job is keying invoices in — cannot advance one. The
      backend already intends managers to assign (`POST /{id}/assign` allows
      admin **and** ap_manager); only the user-list endpoint blocks them.
      **Durable fix:** a minimal assignable-reviewers endpoint (id +
      full_name + is_active, no email or other PII) gated to the roles that may
      assign, and point the picker at it. Do **not** widen
      `GET /api/admin/users`.
      **Trigger:** the next slice touching invoice submission or reviewer
      assignment. Sized: one endpoint, one schema, one store call.
- [ ] **`ScheduledReport` has no CRUD surface at all.** `grep -rn
      "ScheduledReport" backend/app scripts/` finds no router, schema, seed or
      script, and the frontend has zero references — so a row can only be
      created by hand-written SQL and `list_due_schedules` returns `[]` on every
      tick forever. Root `CLAUDE.md` advertises "`/analytics` … + scheduled-report
      CRUD" and `backend/docs/analytics.md` says "an operator re-enables from the
      admin UI"; neither exists. The sweep's own transaction-isolation defect was
      fixed in this round (commit `a4184d08`) so the machinery is sound — it just
      has no way to be reached.
      **Durable fix:** add the `/analytics/scheduled-reports` CRUD router the
      docs already describe, or — if the feature is being dropped — delete the
      model, the sweep and the two flags in one change rather than leaving a
      documented no-op.
      **Trigger:** a product call on whether scheduled reports ship. This is the
      decision, not the work; the work is small either way.
- [ ] **Notification preferences cover 4 of the 7 event types, so
      supplier-chat email cannot be muted.** `frontend/src/lib/types/notification.ts`
      and `backend/app/schemas/notification.py` both enumerate only the four
      `invoice_*` events, while `backend/app/models/notification.py` declares
      seven. `chat_message`, `contract_renewal_due` and
      `cash_shortfall_projected` go through the same `notify_event` writer, and
      `resolve_prefs` defaults a missing key to **on** — so every supplier-chat
      message emails the AP team with no opt-out.
      **Durable fix:** extend both prefs schemas and the frontend union / labels
      / order, with a roster drift guard mirroring
      `tests/test_exception_type_labels`.
      **Trigger:** the next change to notification preferences or the chat
      notification path.
- [ ] **Credit memos are created with no currency, dead-ending non-USD
      tenants.** `frontend/src/routes/credit-memos/+page.svelte` omits
      `currency` and its modal has no such input;
      `backend/app/schemas/credit_memo.py` defaults `"USD"`, and
      `api/credit_memos.py` then 409s any non-USD invoice on apply. There is no
      PATCH on credit memos, so a EUR tenant's memo can never be applied or
      corrected — and it displays as "$500".
      **Durable fix:** a currency select defaulted from the org's reporting
      currency, and default the schema from the invoice when one is named
      rather than from a hardcoded `"USD"`.
      **Trigger:** the next slice touching credit memos or multi-currency.
- [ ] **Vendor bank-change approvals have no UI, so the dual-control gate is
      unreachable from the app.** `/vendors` stages the change and toasts
      "submitted for approval", but `grep -rn "change-requests" frontend/src/`
      finds only the supplier portal's own read, and
      `PERM_VENDOR_BANK_CHANGE_APPROVE` is exported and never referenced. The
      backend queue (`api/vendors.py` `GET /change-requests`) and approve
      endpoint both exist. Vendor banking therefore cannot be updated through
      the app at all.
      **Durable fix:** a `/vendors/change-requests` sub-route beside the
      existing `/vendors/screening`, gated on the permission.
      **Trigger:** the next slice touching vendor management. This is the
      highest-value item in this list — a shipped control with no way to
      exercise it.
- [ ] **Four list surfaces are missing the app-wide request sequencer.**
      `frontend/src/routes/{exceptions,purchase-orders,credit-memos,goods-receipts}/+page.svelte`.
      `frontend/CLAUDE.md` § Sequencing list fetches enumerates the covered
      surfaces and states "a new list surface wires it too"; these four don't.
      Repro: click Load more, then change the filter chip — the page-1 replace
      lands first, then the append pushes page-2 rows of the OLD filter onto the
      new list and overwrites `total`/`page`.
      **Durable fix:** wire `createRequestSequencer()` per the documented
      three-call protocol; extend
      `frontend/tests-e2e/reactivity/local-edit-vs-inflight-fetch.spec.ts`.
      **Trigger:** the next change to any of those four routes.
- [ ] **Filter chips that can never match.** `/expenses` offers `rejected` and
      `reimbursed` (`frontend/src/lib/types/expense.ts`), but the only
      `ExpenseStatus` writers in `api/expenses.py` are `submitted`/`approved`/
      `draft`, and report rejection returns children to `draft`. `/requisitions`
      offers `submitted`, but `RequisitionStatus.submitted` is never assigned —
      submit jumps straight to `pending_approval` (the docstring at
      `api/requisitions.py:335` still advertises the old graph). Both return an
      empty list forever.
      **Durable fix:** either drop the chips, or make the transitions stamp the
      states their names promise — a product call on which of the two each case
      wants.
      **Trigger:** the next slice touching expenses or requisitions.
- [ ] **Mobile offers Amount on an approved invoice, which the backend
      refuses.** `mobile/lib/models/invoice.dart` mirrors `IMMUTABLE_STATUSES`
      but not the narrower `_FINANCIALLY_LOCKED_STATUSES = {approved} |
      IMMUTABLE_STATUSES` (`backend/app/api/invoices.py`). Editing an approved
      invoice's amount 409s with a generic toast, and a combined
      description+amount edit loses the description too. The web client already
      implements exactly this guard (`InvoiceModal.svelte` →
      `invoiceFieldPayload()`), so this is a parity gap.
      **Durable fix:** add `isFinanciallyLocked`, render Amount read-only, and
      omit it from the PATCH diff.
      **Trigger:** the next slice touching the mobile invoice edit sheet.
- [ ] **An orphaned extraction poll un-filters the invoice list.**
      `frontend/src/lib/components/modals/InvoiceModal.svelte` — nothing
      disables Close while `extracting`, and `pollForCompletion` runs up to 60s
      after the modal unmounts, then calls the UNFILTERED `invoiceStore.fetch()`.
      `closeInvoiceModal` has already re-applied the filters, so seconds later
      the list silently becomes unfiltered while the status chips still show the
      filter, and `lastParams` resets so Load-more paginates the wrong set. The
      same hazard is explicitly avoided a few lines above for the approve path.
      **Durable fix:** an `$effect` cleanup setting `cancelled = true`, checked
      after each `await` in the poll, with the refresh going through a
      host-supplied callback.
      **Trigger:** the next change to `InvoiceModal`.
- [ ] **Payment-run Execute is a single unarmed click.**
      `RunDetailModal.svelte` — the one irreversible money-moving control in the
      web app has no arm/confirm, while strictly less consequential actions do
      (cancel-draft two-click in the same footer, void/compliance confirms,
      credit-memo void, API-key revoke). Mobile *does* confirm. Stated fairly:
      `payments/+page.svelte` documents the modal itself as the review surface
      and the button label carries the amount, so this is a judgment call rather
      than a defect of logic.
      **Durable fix:** arm the commit (two-click or a confirm dialog), updating
      `frontend/tests-e2e/payments/execute.spec.ts`.
      **Trigger:** a product call on whether the modal counts as the
      confirmation step.
- [ ] **`tests-e2e/discounts/money-path.spec.ts:228` is date-boundary flaky off
      UTC.** "per-invoice ROI is the exact cost-of-forgoing-discount value"
      asserts `days_accelerated === 20` from an invoice due in 30 days and an
      offer deadline 10 days out. `makeInvoice` and `makeOffer` derive their
      dates separately, so on a machine whose local date differs from UTC the
      two straddle midnight and the assertion sees 21. CI runs in UTC and never
      sees it; it reproduces reliably on a UTC-4 workstation after 20:00 local.
      Diagnosed while triaging PR #315 — the spec is untouched by that PR and
      the ROI primitive itself is correct.
      **Durable fix:** derive both dates from one clock read passed into both
      helpers (or assert the relationship `netDue - discountDeadline` rather
      than a hardcoded 20).
      **Trigger:** the next change to the discounts e2e specs, or the first
      time it costs someone a local triage session.
- [ ] **`api/cards.py::_normalize_charge_amount` divides by a flat 100.** Not
      ISO-4217-exponent aware, unlike
      `payment_adapters.base.minor_units_to_decimal`. Lithic is USD-only in
      practice and Nium is major-unit, so no currency currently in play is
      mispriced — recorded so the next person doesn't re-derive it.
      **Durable fix:** route it through `minor_units_to_decimal`.
      **Trigger:** adding a card provider or a non-USD card currency.

### Surfaced by the money-path bug hunt (round 10), deliberately not fixed

A hunt scoped to the money path (`api/{payments,credit_memos,discounts,cards,tax}`,
`services/payment_*`, `services/discount_*`, `services/billing/`, and the
payment / card / FX / financing adapter families) fixed seven defects at the
root: the 1099 total summing invoice-currency payments as home currency; a
subscription with no billing period, so every plan change prorated `0.00`; the
standalone payment path skipping the financial-integrity exception gate;
`/compliance/release` never handing off to the ERP sync; run creation not being
entity-scoped; `POST /api/payments` accepting an unvalidated `payment_run_id`;
and the financing dispatcher still falling back to `mock`.

These are the remainder. Each was **confirmed by reading the code** (the seven
above were each reproduced with a failing test first); none is fixed here
because each is either outside that scope, or a design call rather than a
defect with an obvious correct answer.

- [ ] **The payment reconciler runs one transaction for a whole tenant and
      holds `FOR UPDATE` locks across network calls.**
      `services/payment_reconciler.py:249-336` — `db.refresh(payment,
      with_for_update=True)` is inside the per-payment loop, the only
      `db.commit()` is after it, so payment #1's row lock is held while
      `await adapter.get_payment_status(...)` runs for #2…#N. Two effects: a
      webhook for a locked payment blocks on `payment_webhook`'s own
      `FOR UPDATE` for the rest of the sweep (and if that request is then
      cancelled, `CancelledError` is a `BaseException` its `except Exception`
      tail doesn't catch, so the Redis dedup claim is never released and the
      provider's retry is deduped away for the full TTL); and any raise
      mid-loop discards every terminal transition, `completed_at` and audit row
      the sweep already decided for that tenant. `_dispatch_run_payments`
      commits per payment for exactly this reason.
      **Durable fix:** commit per payment inside the loop, mirroring
      `_dispatch_run_payments`.
      **Trigger:** the next change to the reconciler, or enabling
      `FEOH_PAYMENT_RECONCILE_ENABLED` in a deployed env.
- [ ] **An aged-out payment silently frees the invoice for a second payment.**
      `services/payment_reconciler.py:254-278` — past
      `FEOH_PAYMENT_RECONCILE_MAX_AGE_HOURS` (72h) a still-`submitted` payment
      (real money possibly in flight) is flipped to `failed`, which is in
      `LIVE_PAYMENT_TERMINAL_STATUSES`, so it leaves the
      `uq_payments_one_live_per_invoice` slot. The invoice stays
      `payment_scheduled` — a `PAYABLE_INVOICE_STATUSES` member — so it
      reappears in `GET /payments/queue` and a fresh run will happily pay it
      again. No exception row, no flag. The asymmetry is the tell:
      `_RETRY_SAFE_FAILURE_PREFIXES` deliberately excludes
      `reconciler_max_age_exceeded` so `/retry-failed` fails closed on exactly
      this row, while the fresh-run path fails wide open on the same invoice.
      Secondary: it stamps `completed_at` on a payment that never completed.
      **Durable fix:** open a de-duped `erp_reconciliation`-style exception on
      aged-out (which is payment-blocking, so a new run refuses the invoice
      until a human reconciles the rail), and stop writing `completed_at`.
      **Trigger:** enabling the reconciler in a deployed env.
- [ ] **The cash-flow what-if claims discounts whose window has already
      closed, and buckets their outflow into the past.**
      `services/analytics.py:786-790` — the `early` scenario takes
      `discount_date` unconditionally, with no `today <= discount_date` check,
      while `api/analytics.py::_commitment_rows` bounds only the *due* date to
      the horizon. A plain 2/10-net-30 invoice still open on day 15 is reported
      as capturing 2% and has its outflow placed in the week containing day 10,
      five days in the past — a negative term in `weighted_avg_pay_date_days`.
      `bucket_outflows`' `discount_eligible_amount` (`analytics.py:718-723`)
      has the same blind spot. Every other consumer of the same economics
      enforces the window (`discount_offers._tier_achievable`,
      `discount_optimizer.optimize`'s `capturable = today <= opp.pay_by`), and
      `tests/test_analytics.py` only ever uses a future `discount_date`.
      **Durable fix:** gate the `early` branch and `discount_eligible_amount`
      on `today <= discount_date`, matching the optimizer.
      **Trigger:** the next change to `/analytics/cashflow_whatif` or the
      copilot's `run_payment_whatif` tool.
- [ ] **`_commitment_rows` computes an `unconverted` flag nothing reads.**
      `api/analytics.py:188-202` sets it per row and its docstring promises
      "'we could not convert this' is visible rather than silent", but no
      consumer reads the key: not `bucket_outflows`, not
      `/analytics/{cashflow_forecast,cashflow_whatif,cash_position}`, not any
      of the four copilot tools, not `cash_flow_alerts`. A €100,000 invoice
      with a NULL `reporting_amount` is subtracted from a USD cash curve as
      $100,000 and feeds the shortfall-alert email with nothing saying so.
      Contrast the expense path, which surfaces `unconverted_count` and fails
      the CFO gate closed.
      **Durable fix:** carry the count out of `_commitment_rows` and surface it
      on the forecast / cash-position / plan responses, the way `card_paid` and
      `unconverted_payment_count` are surfaced on the 1099 report.
      **Trigger:** the next multi-currency slice, or the first non-USD tenant
      on the copilot.
- [ ] **The discount optimizer and the plan assembler drop the offer's
      currency.** `api/discounts.py:159-170` builds an `OfferOpportunity` from
      `offer.base_amount` and never carries `offer.currency` (the dataclass has
      no such field), so `discount_optimizer.optimize` compares outlays against
      `cash_budget` as bare numbers, and `cash_flow_plan.assemble_plan`
      substitutes an offer-currency row for a reporting-currency commitment row
      on the cash curve. A ¥10,000,000 offer in a USD-reporting org puts a
      ~$9.8M outflow on the curve and fabricates a shortfall the copilot then
      narrates around. Same family: `/discounts/dashboard` sums
      `captured_amount` across currencies and labels it `_org_currency(org)`,
      and `build_bulk_offer` sums a vendor's open `Invoice.amount` values raw.
      This is the same class as the 1099 defect fixed this round, which now
      leaves a ready-made primitive.
      **Durable fix:** carry `currency` on `OfferOpportunity` and refuse (or
      convert via the invoice's locked `reporting_amount`) any offer not in the
      reporting currency before it reaches `optimize` / `assemble_plan`;
      exclude-and-count in the dashboard, mirroring
      `currency_conversion.payment_reporting_amount_sql`.
      **Trigger:** a multi-currency tenant using dynamic discounting, or the
      next change to the optimizer.
- [ ] **`/payments/summary` and `/payments/queue` sum `Payment.amount` across
      currencies.** `api/payments.py:406-424` / `:346-403` — no grouping, no
      currency in the response, no `source_amount` preference. Same defect the
      1099 report carried until this round, and the same one
      `services/compliance.py:199-215` and `docs/multi-currency.md` already
      call out elsewhere. Also `api/analytics.py:766-775` (`paid_in_period` →
      `avg_daily_outflow` → `wc_impact_5d`) and
      `services/vendor_risk_scoring.py:145-157`. Reporting surfaces only — no
      money moves on them. Minor, same area: `total_pending` omits
      `pending_compliance`, so held money appears in neither KPI.
      **Durable fix:** route each through
      `currency_conversion.payment_reporting_amount_sql` (added this round) and
      surface the excluded count, as the 1099 report now does.
      **Trigger:** the next multi-currency reporting slice.
- [ ] **`PaymentRun.status` is never recomputed after the dispatch pass, and
      the rollup fails open on an all-`pending` run.**
      `services/payment_runs.py:67-81` returns `completed` when nothing is
      completed, failed or in flight — a fail-open default on a money-run
      status. And the only writer of the persisted `PaymentRun.status` after
      execution is `_dispatch_run_payments`'s final rollup: neither the
      webhook, the reconciler, nor `/compliance/{release,dismiss}` touches it.
      So a run that rolled up `submitted` (one payment held
      `pending_compliance`) and then had that payment dismissed reports
      `status: "submitted"`, `payments_failed: 1`, `retryable_failures: 1` —
      and `/retry-failed` 409s because `RETRYABLE_RUN_STATUSES` is
      `("partial", "failed")`. `/resume` and `/execute` 409 too: a dead end,
      and precisely the "button that can't act" the `retryable_failures`
      comment says it exists to prevent. Not reproduced end to end (the
      all-`pending` half needs two interleaved `/resume` calls); the code paths
      are confirmed by reading.
      **Durable fix:** derive the run status from its payments on read (or
      recompute it wherever a payment's status changes), and give the rollup a
      non-`completed` answer for an all-`pending` run.
      **Trigger:** the next report of a run stuck with a retryable failure it
      won't retry.
- [ ] **`void_payment` overwrites the regulated `completed_at`.**
      `api/payments.py:745-748` stamps `completed_at = now` when voiding a
      `completed` payment, destroying the real settlement timestamp; the audit
      row records `previous_status` but not the previous timestamp.
      `/retry-failed` explicitly refuses to overwrite the same two timestamps
      and says why. Every downstream reader filters `status == "completed"`, so
      the loss is evidentiary rather than arithmetic.
      **Durable fix:** leave `completed_at` alone and record the void instant
      on its own field (or only in the audit row).
      **Trigger:** the next SOX evidence review, or a migration that adds a
      `voided_at`.
- [ ] **`/compliance/release` doesn't guard `_execute_single_payment`.**
      `api/payments.py` — every other caller wraps it because "a live FX /
      sanctions / processor adapter can raise anything", and marks the payment
      `failed` so the attempt is recorded. Here it propagates: FastAPI 500s,
      the session rolls back, and the payment reverts to `pending_compliance`
      with no `provider_payment_id` recorded even if the processor accepted the
      order. On the card leg a rollback after `persist_card` discards the
      `VirtualCard` row while a real spendable card exists at the provider.
      The reused `correlation_id` (the processor's idempotency key) mitigates
      for rails that honour it, but is not a substitute for the record.
      **Durable fix:** wrap the call the way `_dispatch_run_payments` does.
      **Trigger:** the next change to the compliance-hold endpoints.
- [ ] **Smaller confirmed items, grouped** — each read and confirmed, none
      worth its own entry:
      `api/discounts.py::create_offer` resolves the invoice for an
      invoice-scoped offer with no `apply_entity_scope`, so an offer can be
      created under entity A against entity B's invoice (advisory data, never
      money — but the sibling credit-memo path was fixed for exactly this).
      `api/cards.py::card_dashboard`'s `rebate_ytd` filters
      `CardRebate.period >= f"{year}-01"` with no upper bound, so a row with a
      future period would leak into YTD.
      `POST /api/cash-flow/plans/{id}/capture-discounts` mutates offers with no
      row lock (status-only, so the worst case is a duplicate audit row).
      `_commitment_rows`' `outerjoin(PaymentSchedule)` is un-deduped, so an
      invoice with two schedule rows is double-counted at full amount —
      unreachable today because `PaymentSchedule` is only ever constructed in
      `scripts/seed.py`, but `discount_auto_trigger._resolve_due_date` already
      takes the latest-schedule precaution the forecast path skips.
      `cashflow._coerce_decimal` returns `None` for a malformed persisted
      `min_balance_threshold` and accepts `Decimal("NaN")`, either of which
      silently opts an org out of shortfall alerting with no log line.
      Three analytics endpoints use `date.today()` (host local) where every
      other consumer of the same data uses UTC, so a non-UTC host can shift the
      horizon filter and the week bucketing by a day — and `plan_id` embeds
      `today.isoformat()`.
      `POST /api/cash-flow/plans/{id}/draft-run` can only ever 422 for a
      multi-currency tenant, because it hands every payable invoice in the
      horizon to a builder that refuses a multi-currency run; the plan card's
      button can never succeed there and the error isn't actionable from a plan
      the user can't edit.
      `_execute_single_payment` skips the FX leg, the credit-memo re-check
      **and the entire compliance gate** when `invoice is None` — the inverse
      of the two carefully-argued "no screenable vendor → hold, never pay
      unscreened" branches directly above it; unreachable today only because
      deleting an invoice cascades its payments.
      **Durable fix / trigger:** each in the file it names, on the next slice
      that touches it.

### AI Cash-Flow Copilot — Phase 3 deferred bucket

Phases 1–3 core shipped (read-only cash Q&A, `propose_payment_plan` +
`PlanCard`, and draft-run/capture-discounts enactment — see
[roadmap.md](roadmap.md) § AI Cash-Flow Copilot). Only the
originally-deferred sub-bucket from that same feature remains:

- [ ] Saved plans / plan-vs-actual (`CashPlan` model + migration)
- [ ] Consolidated cross-entity mode

**Trigger:** next feature slice. Nothing blocks it.
Refs: [roadmap.md](roadmap.md) § AI Cash-Flow Copilot,
[cash-flow-copilot.md](cash-flow-copilot.md).

### Tinted badges are spelled ~40 ways for five tones

The 29 badges that sat **below** 4.5:1 on a translucent tint are fixed — option
1 of the three shapes this entry used to weigh (tint-paired text tokens), with
values promoted out of `StatusBadge`, which had already solved it for its own
tones by lifting the text rather than darkening the tint. The scanner's
compositing half is armed, the palette contract asserts each pair over both
backdrops, and `frontend/src/lib/a11y/tokenPairing.test.ts` fails on a
recurrence. Rationale, and why the other two shapes were rejected:
[decisions.md](decisions.md) §30.

What remains is the **consistency** half, which is not a contrast problem:
**202 rules** still write a tinted badge as a hand-rolled `rgba()` plus a
literal hex — **44 distinct spellings** of the five tones the tokens now name.
(`StatusBadge`, the shared primitive the values were derived from, is already
migrated — it was where the numbers the other badges copied lived.) A sample of
how thin the distinctions are:

| Spelling | Count |
|---|---|
| `var(--danger)` on `rgba(224,64,64,.1)` | 20 |
| `#d4940a` on `rgba(212,148,10,.12)` | 19 |
| `#1fa86a` on `rgba(31,168,106,.15)` | 18 |
| `#1fa86a` on `rgba(31,168,106,.12)` | 18 |
| `#d4940a` on `rgba(255,180,50,.15)` | 11 |
| …plus ~35 more, mostly single-digit | |

**Every one of these passes**, so this is design-system debt, not a defect —
which is exactly why it is here and not in
[known-issues.md](known-issues.md). It still matters: the same tone written
four ways is how the 29 failures accumulated unnoticed, and a file where one
rule is tokenised while its three siblings are literals reads worse than either
extreme (`ContractModal`'s `.badge.draft` next to `.active` / `.expired` /
`.terminated` is the canonical example).

**Why deferred rather than swept now:** it is ~7× the size of the failing set,
and it is not value-preserving. The tokens standardise on alpha `.15`, so
normalising a `.1` or `.12` rule visibly strengthens that badge's tint. That
is a design review across most of the app's surfaces, not a mechanical
substitution — and landing it in the same change as the contrast fix would make
any visual complaint impossible to attribute to one or the other.

**Durable fix:** sweep by tone, one commit per tone, checking the rendered
result rather than only the guard — the guard is already green on all of them
and will stay green either way, so it cannot be the reviewer here. Retire each
literal as it moves. Expect a few genuine one-offs like `StatusBadge`'s purple
(`#a585f5`, `sent_to_erp`): it shares no semantics with the five tones, so it
stays a measured literal with a comment saying so rather than becoming a token
with one caller. And check what a collapsed distinction was carrying before
collapsing it — see the `pending_compliance` note in §30.

**Trigger:** the next `/audit:accessibility` pass, or opportunistically —
reach for the tokens in new code and whenever you are already editing one of
these rules. Refs: [accessibility.md](accessibility.md),
[decisions.md](decisions.md) §28 + §30, `frontend/CLAUDE.md` § Colour tokens
and contrast.

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

- [ ] **Confirm Teams posts the approval card's action body byte-for-byte.**
      The outbound card stamps each Approve/Reject `HttpPOST` action with the
      HMAC of the exact `body` string it will send, and
      `/api/approvals/teams/interactivity` re-derives it over the raw request
      bytes ([decisions §33](decisions.md)). If Microsoft re-serialised the body
      rather than relaying it verbatim, the digest would not match. The failure
      mode is graceful and already tested — the opaque ack tells the approver to
      sign in to the app, never a 500 or a wrong decision — but only a live
      Teams tenant can confirm the happy path.
      **Durable fix:** post a real card into a Teams channel, click both
      buttons, and confirm the invoice transitions; if the body is re-serialised,
      switch the digest to cover a canonical subset (the action token alone)
      rather than the whole string.
      Ref: [teams-approval.md](../backend/docs/teams-approval.md).

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
