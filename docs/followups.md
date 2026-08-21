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

Mirrored as GitHub issue [#321](https://github.com/Absence0760/project-account-payables/issues/321)
for the tracker view. Keep the two reconciled when either moves.

**Last reconciled:** 2026-08-20 against round 14 — a five-agent parallel bug
hunt across the money path, auth/tenant isolation, the SvelteKit frontend, the
background sweeps and adapter registries, and procurement/analytics. Twenty-nine
defects were fixed and committed with regression tests. The findings each agent
verified in the source but correctly did not fold in are recorded below, one
subsection per area, immediately above § (a).

**Previously reconciled:** 2026-08-19 against round 13 — a four-agent sweep of the
**codeable** half of this file (the `(a)` credential-blocked and `(b)` operator
items have no code to write, and four `(c)` items are gated on a product call or
a third-party artifact). It closed both consistency-debt items, both transitional
frontend surfaces, the whole Cash-Flow Copilot Phase 3 bucket, and took the badge
sweep 62 → 30 rules. 21 open items → 20 — the count barely moved because three
agents surfaced four *new* findings while closing seven old ones, which is the
file working as intended. Calls recorded in [decisions.md](decisions.md) §50-§55.

Three of those closures found a live defect the entry had not predicted: the
email regex admitted a trailing newline into SMTP headers, the `utc_today` drift
guard was blind to the very spelling two modules used, and converting a badge to
the standard `.15` tint pushes any `--text-muted` sub-label *inside* it below
4.5:1.

Before that: 2026-08-19 against round 12 — a five-agent sweep of the
open follow-up backlog itself (money path, vendors/procurement/expenses,
multi-currency/e-invoicing/async, ingest/reports/AI, and the frontend), plus a
sixth pass landing the backend legs the frontend work was written against. The
backlog went from 75 open items to 21; the calls each agent had to make are
recorded in [decisions.md](decisions.md) §36-§49.

Before that: 2026-08-18 against round 10 — a five-agent bug hunt.
Each agent fixed its findings at the root with a reproducing test first, and
recorded what it confirmed but did not fix in its own section below. The pass
is **additive**: nothing pre-existing was closed or re-verified, so every
earlier entry stands as it was left on 2026-08-17.

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

### One adapter family still ships code no caller reaches

- [ ] **`services/financing_adapters` has no production caller.** The
      supply-chain-finance family (`mock` + the `c2fo` skeleton) is built,
      registered and tested, but nothing in `app/` selects a financing provider
      or requests funding. The Protocol violation that used to sit here is
      **fixed** — `C2FOAdapter.quote` / `.request_funding` return an ineligible
      `FinancingQuote` / unfunded `FinancingFundingResult` with
      `reason="provider_not_implemented"` rather than raising
      `NotImplementedError`, pinned by three tests in
      `tests/test_financing_adapters.py`.
      **Why still deferred:** unlike the corridor auction (now wired as an
      advisory read — [decisions.md](decisions.md) §42), financing has **no safe
      read-only half**. A financing quote is only meaningful if it can be
      accepted, and accepting it moves money to a supplier from a third-party
      financier — so wiring it up *is* the product decision about whether the
      platform offers supply-chain financing at all, not a step toward it.
      **Durable fix:** a product call, then the accept path with its own
      approval + audit story.
      **Trigger:** a decision to offer supplier financing.
      Ref: `backend/docs/dynamic-discounting.md`.

- [ ] **`modern_treasury` publishes no fee table, so it is skipped by the
      corridor auction.** `compare_quotes` now has a production caller —
      `POST /api/payments/corridor-quotes`, advisory and read-only — but an
      adapter with no fee schedule correctly reports `no_quote_endpoint` and
      drops out of the ranking, so a tenant on Modern Treasury sees an auction
      its own rail never enters.
      **Durable fix:** its real pricing, transcribed into the adapter's fee
      table. This is data, not code.
      **Trigger:** obtaining Modern Treasury's contracted pricing.
      Ref: `backend/docs/international-payments.md` § Multi-route quote
      optimization.

### E-invoice conformance is checked by our own code, not the official validators

Both generators were corrected this round to meet their standards
([decisions.md](decisions.md) §44, §45), and both are pinned by structural tests.
Neither is validated against the authority that will actually judge it.

- [ ] **BIS Billing 3.0 conformance is a hand-written mandatory-element pass,
      not the official Schematron.** `services/e_invoice/bis3.py::bis3_conformance_errors`
      covers the rules the normalized model can answer, and it gates whether we
      declare the profile at all — so a document that **fails** it provably does
      not conform, which is what makes the conditional declaration sound. It does
      not evaluate the calculation rules (BR-CO-*) or code-list membership, so a
      document that **passes** can still fail the real validator.
      **Durable fix:** vendor the official EN 16931 + PEPPOL Schematron into
      `backend/tests/fixtures/` and assert generated documents validate in CI.
      **Trigger:** the PEPPOL `as4_gateway` slice.

- [ ] **No FatturaPA XSD in the repo, so the generator is validated by
      inspection.** The root-only namespace-qualification fix cites the v1.2
      schema's `elementFormDefault` default and is pinned by a structural test,
      but nothing validates a generated instance against the real XSD.
      **Durable fix:** vendor the v1.2 XSD into `backend/tests/fixtures/` and
      assert the document validates.
      **Trigger:** the SdI clearance slice.

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

### Consistency debt the round-12 sweep surfaced rather than introduced

The email-regex entry that stood here is **closed** — hoisted to
`app/utils/emails.py::looks_like_email` with a drift-guard test. Closing it found
a live hole: all three copies ended in `$`, which in Python matches end-of-string
*or just before a trailing newline*, so `"user@example.com\n"` passed every check
and was stored as a login, a child-tenant admin address and a scheduled-report
recipient — a newline reaching an SMTP header is the header-injection primitive.
The shared pattern anchors with `\Z` ([decisions.md](decisions.md) §50).

The `date.today()` sweep is likewise done: **no `.today()` call remains anywhere
under `backend/app/`**, and `tests/test_utc_today.py` now guards 31 modules with a
scanner that catches `date.today()`, `datetime.today()`, `datetime.date.today()`
and naive `datetime.now().date()`. Widening it exposed a hole in the guard itself
— it matched only `ast.Name`, so the attribute-shaped `datetime.date.today()` was
invisible, which is exactly what both Positive Pay modules used; either could have
sat on the "converged" allowlist while still reading local time
([decisions.md](decisions.md) §51). What is left is cosmetic:

- [ ] **Six `datetime.now(UTC).date()` sites are correct but unguarded.**
      `api/api_keys.py`, `api/bank_reconciliation.py`,
      `services/discount_auto_trigger.py`, `services/contract_renewal.py`,
      `services/recurring_invoices.py` and
      `services/financing_adapters/mock_adapter.py` still inline the expression
      instead of importing `utc_today`, so they cannot join `UTC_TODAY_MODULES`
      and sit one careless edit from regressing silently.
      **Durable fix:** swap the expression, add each to the allowlist.
      **Trigger:** the next change to any of them.

### Tinted badges — the shared primitive exists, half the call sites still hand-roll

The contrast half of this entry is long closed (the 29 badges below 4.5:1, fixed
via tint-paired text tokens — [decisions.md](decisions.md) §30). Round 12 closed
the **ownership** half: `frontend/src/lib/components/ui/Badge.svelte` is now the
single owner of the tinted-badge recipe. A caller names a *tone* and cannot spell
it wrong; `variant` passes the caller's semantic class through as a **selector
hook only** (the e2e suite reads `.badge.approved`), never as colour. Rationale,
including why sizing is fixed rather than a prop and why `neutral` / `erp` stay
non-tinted: [decisions.md](decisions.md) §47.

- [ ] **30 badge-shaped CSS rules still hand-roll the recipe.** Down from 62.
      This tranche converted `/payments` (14 → 2), `/admin/webhooks` (5 → 0),
      `RequisitionModal` (8 → 0) and `ExpenseModal` (6 → 0) in four attributable
      commits; the remainder is `/expenses` (13), `/requisitions` (8) and
      `InvoiceModal` (7).
      **No distinction was lost.** `pending_compliance`'s ring — the thing that
      says "a human must clear this", where the tone alone says only "waiting" —
      was preserved as a caller-owned wrapper rather than flattened or turned into
      a one-caller prop ([decisions.md](decisions.md) §52). Every other
      consolidation (`cancelled`/`voided`, runs `submitted`/`executing`, six of
      eight card statuses) merged partners that had **no rule at all** and rendered
      untinted — the conversion's real payoff on `/payments` was the exhaustiveness
      check, not fewer lines. Two chips stay off the primitive by design
      (`.discount-chip` is two stacked lines; `.blocked-chip` wraps a localised
      sentence where `nowrap` would break 320px reflow); both took the tokens.
      **The count is scoped to the seven files this entry names, and that
      undercounts:** `RunDetailModal.svelte` hand-rolls 7 more, and
      `routes/+page.svelte` carries a second spelling of the `.overdue-badge` this
      tranche retired — so that flag now renders at two sizes on two pages until it
      converts. Both should join the next tranche.
      **Durable fix:** convert the rest in attributable tranches, checking
      collapsed distinctions as you go, and hoist the two tone maps out of the
      modals into `types/{requisition,expense}.ts` in the same change.
      **Trigger:** the next slice touching `/expenses`, `/requisitions` or
      `InvoiceModal`.

### Surfaced by the round-13 sweep

Four items the round-13 agents confirmed but correctly did not fold into their
own tranches.

- [ ] **`opacity` used to de-emphasise text drops it below 4.5:1.**
      `tr.inactive td:not(.actions) { opacity: 0.6 }` on `/admin/webhooks` renders
      every cell of a paused subscription's row — status pill included — at
      **2.95:1** (measured). Neither guard sees it: the `cssAudit` composited check
      resolves a rule's *own* opacity, not an ancestor's, and the axe route scan
      only catches it when an inactive row happens to be rendered. It is an
      app-wide convention at ~20 sites; the disabled-control ones are exempt under
      WCAG 1.4.3, the text rows are not.
      **Durable fix:** de-emphasise with a muted *colour* token instead of opacity,
      and extend `cssAudit` to carry an ancestor's opacity into the composited
      check so the guard finds the rest.
      **Why not folded into the badge tranche:** a whole-table visual change would
      make the badge conversion unattributable, which is the entire reason that
      work is tranched.
      **Trigger:** the next a11y sweep, or any change to that row treatment.

- [ ] **`GET /api/expenses/export` has no `search` leg.** The list searches
      merchant / description / category; the export does not, so a CSV taken
      during a search covers the whole status-filtered set. Nothing is misleading
      today — the frontend refuses to pretend, keeping a separate
      `buildExportParams()` pinned by an e2e — but the two surfaces disagree about
      what "filtered" means.
      **Durable fix:** add `search` to `export_expenses`, reusing `list_expenses`'
      own `or_(...)` predicate rather than restating it, then point the button back
      at `buildParams()`.
      **Trigger:** the next slice touching the expense export.

- [ ] **`InvoiceModal`'s supplier-chat @mention autocomplete has no member source
      on `/invoices`.** It reads `adminStore.users`, which only `/admin` or
      `/workflows/[id]` populate. Pre-existing, and now visible: the deleted
      admin-only approver fallback used to populate that store as a side effect,
      for admins, in the failure case only.
      **Durable fix:** a non-admin-readable member list shaped like
      `assignable-reviewers` but *not* gated on `invoice.approve` — mentions are
      broader than approvers — and explicitly not `GET /api/admin/users`.
      **Trigger:** the next slice touching supplier chat.

- [ ] **Two keystroke-debounce guards duplicate a technique instead of joining its
      table.** The `/requisitions` and `/expenses` specs each carry their own
      "typing N characters fires one request" test rather than joining the
      parameterized `CASES` array in
      `tests-e2e/reactivity/search-debounce-race.spec.ts`. They were written
      locally to avoid a merge conflict on that shared file during the parallel
      round.
      **Durable fix:** fold both cases into that array and drop the local copies.
      **Trigger:** the next change to either spec.

### Surfaced by the round-14 auth / tenant-isolation sweep

- [ ] **`RealDB.client()` overrides `get_tenant_db` wholesale, so no realdb test
      ever runs the `get_tenant` JWT-org cross-check.** That cross-check is the
      control tenant isolation actually rests on ([decisions §1](decisions.md)),
      and most tenant-data routes reach it only as `get_tenant_db`'s own
      dependency — so an override that replaces the provider replaces the guard
      too. Measured: with the harness client, tenant A's token plus tenant B's
      `X-Tenant-Slug` returns **200**; on the production chain the same request
      is a **403**. Nothing is broken today — this is a blind spot, not a
      defect — but it is exactly the shape of the late-commit override recorded
      in [decisions §20](decisions.md), where an override that quietly changed
      semantics is *why the suite never caught* the real bug underneath it.
      **Closed for now at the narrow end:** `tests/test_tenant_isolation.py`
      gained three end-to-end cases that override only `get_control_db` and let
      the real `get_tenant_db` run (a mutation of the guard turns them red).
      **Durable fix:** give the harness override the same dependency the real
      one has — `async def _tenant_db(request: Request, tenant: Organization =
      Depends(get_tenant))` — so every realdb test exercises the cross-check
      for free, the way the overrides already mirror `commit_before_response`.
      **Why not now:** it changes the dependency chain under every realdb test
      in the suite (a slug-swapping isolation test that currently gets a
      harness session would start getting a 403/404), and validating that needs
      the full ~1-2h suite rather than a scoped run.
      **Trigger:** the next change to `tests/conftest.py`'s `RealDB.client()`,
      or the next full-suite run someone is already paying for.

- [ ] **`/api/auth/login` leaks account existence through the audit write, not
      the password check.** The handler is careful about timing — it calls
      `dummy_verify()` on the unknown-address path specifically so the bcrypt
      cost matches. But a *known* address (wrong password, no password, or
      deactivated) additionally awaits `dispatch_auth_audit`, which resolves the
      tenant DB and commits an `auth.login.failure` row inline; a genuinely
      unknown address has no org, so the write is skipped entirely. The two
      paths therefore differ by a whole DB round trip. In practice it is weak —
      single-digit milliseconds against a ~250 ms bcrypt baseline, and the
      per-account failure budget caps sampling at 10 attempts / 15 min per
      address — which is why it is recorded rather than patched: the obvious
      "fixes" are either dropping the audit (worse) or padding the fast path
      (masking, forbidden by guard rail 4).
      **Durable fix:** move the login-failure audit off the response path (queue
      it the way `services/post_commit` queues notification legs) so *neither*
      branch pays for it, then keep the two branches structurally identical.
      **Trigger:** the next change to the login handler or to
      `dispatch_auth_audit`'s call discipline.
### Surfaced by the round-14 sweep of the background sweeps and adapter registries

Verified in the source and left unfixed: each is a distinct area with its own
blast radius, and folding them into the six landed fixes would make none of them
attributable. Every one is a confirmed reading of the code, not a hypothesis.

- [ ] **`payment_reconciler` holds a `FOR UPDATE` across a processor HTTP call,
      and never releases it on its two skip paths.**
      `payment_reconciler.py` takes `db.refresh(payment, with_for_update=True)`
      and then calls `_settle_from_poll` → `record_settlement` →
      `await adapter.fetch_settlement(...)` — a live rail round trip — before the
      commit that releases the lock. `payment_webhook` takes the same row lock, so
      a real webhook for that payment blocks for the whole fetch. Separately, both
      re-check branches `continue` **without** `await db.rollback()`, so the lock
      survives into every subsequent `await adapter.get_payment_status(...)` in
      that tenant; `approval_escalation` and `extraction_reaper` both roll back on
      the identical skip path and say why in a comment.
      **Durable fix:** `await db.rollback()` on both skip paths, and move the
      settlement fetch outside the lock (resolve the figure, then re-lock to write
      it) — the shape `payment_erp_sync` uses when it resolves the ERP adapter
      *before* taking the invoice lock.
      **Also:** `ReconcileResult` has only `failures` ("tenants we couldn't
      reach"), incremented for a whole-tenant abort. A per-payment
      `adapter.get_payment_status` raise is caught, logged at INFO, and counted
      nowhere — a processor API that is 100% down yields `polled=N, resolved=0,
      failures=0`, which `sweep_health` reports as a healthy tick. Add a
      `payment_failures` counter (the `*_failures` suffix is what
      `sweep_health.failure_count` sums) and raise that log to WARNING.
      **Trigger:** the next slice touching the reconciler or the settlement path.

- [ ] **`discount_auto_trigger` and `contract_renewal` still run one transaction
      per tenant.** Both load candidates unbounded, mutate in a loop, and commit
      once at the end, so a single bad row discards the whole tick's work — in
      `contract_renewal`'s case across two independent passes (a failure in the
      expiry pass rolls back every `renewal_alert_sent_at` the alert pass just
      stamped, and vice versa). If the failure is deterministic that tenant never
      makes progress again.
      **Durable fix:** commit-per-item with a per-item `try` / `rollback` and a
      `*_failures` counter — the shape `vendor_rescreen`, `recurring_invoices`,
      `scheduled_reports` and (as of round 14) `extraction_reaper` /
      `approval_escalation` all use, now written up in
      `backend/docs/background-sweeps.md` § Locking.
      **Trigger:** the next slice touching either sweep.

- [ ] **`vendor_rescreen` and `recurring_invoices` skip step 2 of the documented
      two-phase shape.** Both call `db.get(Model, id)` with **no**
      `with_for_update=True` and never re-check the predicate the id query used.
      `background-sweeps.md` § Locking calls that re-check "correctness, not an
      optimisation". With replicas, two `vendor_rescreen` sweeps both bill a
      third-party sanctions call and write two append-only `SanctionsCheck` rows
      for one screening event; and `uq_invoice_recurring_period` does not cover
      `recurring_invoices`' failure mode — replica A can advance `next_run_on` to
      P+1 and commit while replica B, reading fresh, generates an invoice for a
      period that is not due yet.
      **Durable fix:** `with_for_update=True` plus the predicate re-check
      (`status == active`, `next_run_on <= today`, the staleness cutoff) under the
      lock.
      **Trigger:** the next slice touching either sweep, or the first deploy that
      runs more than one replica.

- [ ] **`qms_sync` accepts a `since` cursor and drops it, then writes an audit row
      per record unconditionally.** `run_qms_sync_once(*, since=None)` never
      references `since`, `_sweep_tenant` takes no cursor, and
      `adapter.fetch_inspections()` is called with no argument even though the
      adapter contract is `fetch_inspections(*, since=None)`. So every tick
      re-fetches the tenant's whole inspection history — and because the
      `quality_inspection.synced` audit write is unconditional (`change` is
      `"updated"` even when nothing changed), each hourly tick appends
      `len(records)` rows to the append-only, WORM-shipped `audit_log`. That table
      cannot be deleted from (migration 0022's BEFORE-DELETE trigger), so it is
      unbounded growth in exactly the table the audit shipper drains.
      **Durable fix:** thread `since` through `run_qms_sync_once` →
      `_sweep_tenant` → `sync_tenant_inspections` → `fetch_inspections`, persist
      the high-water mark per org, and write the audit row only on a real
      create/update. Also surface the computed-then-discarded `skipped` count on
      `QMSSyncResult` (as `templates_skipped` is on `recurring_invoices`).
      **Trigger:** before `FEOH_QMS_SYNC_ENABLED` is turned on anywhere.

- [ ] **`retention_sweep`'s no-op-manifest guard cannot hold.** It writes the
      `retention.archived` manifest when `archived or overdue_total`, but
      `overdue_total` counts `audit_log` rows past the window and the sweep never
      deletes audit rows — so once a tenant crosses its window the condition is
      permanently true and a manifest row is written every tick with
      `invoices_archived: 0`. Each manifest is itself an `audit_log` row that
      becomes overdue later and inflates the next tick's count.
      **Durable fix:** gate on the actionable signal (`archived or
      overdue_unshipped`) or on a change against the previously recorded counts.
      **Trigger:** before `FEOH_RETENTION_ENABLED` is turned on anywhere.

- [ ] **`billing/dunning_sweep` has no per-row guard and no failure counter.** A
      raise on `control_db.commit()` aborts the remaining `past_due` rows for the
      tick (the module docstring claims the opposite), and `_dunning_tick` returns
      a bare `int`, which `sweep_health.extract_counts` maps to `{"count": n}` —
      no `failures` key, so this sweep can never report anything but `ok` short of
      the tick raising outright. The cancellation itself also commits whether or
      not `dispatch_auth_audit` (fail-soft by design) actually wrote the
      `billing.subscription_canceled` row.
      **Durable fix:** per-row `try` / `rollback`, a result dataclass with
      `failures`, and treat a swallowed audit write as a failure rather than a
      silent success.
      **Trigger:** before `FEOH_BILLING_DUNNING_ENABLED` is turned on anywhere.

- [ ] **`audit_log_shipper` head-of-line-blocks a tenant on one unshippable row.**
      Batches are all-or-nothing and ordered `created_at ASC`, so a row whose
      `details` JSONB the sink refuses makes `adapter.ship` raise on every tick and
      no newer audit row for that tenant ever ships — the WORM evidence trail stops
      there. Ranked below the others only because `ShipResult.failures` does
      increment, so `sweep_health` correctly goes `degraded` and the
      `NOT MAKING PROGRESS` line fires; the defect is that the remedy is manual.
      **Durable fix:** quarantine the poison row (ship it with a PII-free
      truncation marker, the way `cloudwatch_adapter` already handles an oversized
      single event) so the trail keeps moving.
      **Trigger:** the first time the alert fires in a deployed env, or the next
      slice touching the shipper.

- [ ] **Four more adapter registries fail OPEN on an unregistered provider name.**
      `docs/decisions.md` §29 fixed this for payments / ERP / FX / extraction and
      §36 for sanctions; round 14 fixed `billing_adapters` because its fallback
      reached a PUBLIC webhook route. These remain, each verified in the source:
      `card_adapters` (`get_card_adapter` → `mock`, whose `create_card` returns
      `success=True` with a fabricated PAN that `card_issuance` then persists as
      an issued `VirtualCard` — a money path §29 did not reach);
      `tax_filing_adapters` (→ `mock`, which returns `BATCH_ACCEPTED` with a
      `MOCK-…` confirmation that `api/tax.py` persists, so an org is told its
      1099s were e-filed when nothing reached the IRS);
      `tin_validation_adapters` (→ `mock`, format+checksum only, yet
      `vendor.tin_verified_at` is stamped from it — driving B-notice / 24%
      backup-withholding decisions off a regex);
      `positive_pay_adapters` (`bank_format` is a free-form `str(max_length=30)`
      with no allowlist and an unknown value silently renders `csv` while the row
      is labelled with the requested format — the operator believes the bank got
      its layout and the cheque-fraud control never applies).
      **Durable fix:** §29's rule — absent/empty still resolves `mock` (local-first
      default), a NAMED provider with no adapter raises, and each caller decides
      what the refusal means. For `positive_pay` the cheaper half is an allowlist
      on the `bank_format` field itself, validated against the registry rather
      than a restated literal.
      **Trigger:** take them one registry at a time, cards first (it is the money
      path), each with the caller-by-caller refusal table §29 established.

- [ ] **`aws_textract` is the one adapter that blocks the event loop.**
      `boto3.client("textract", ...)` + `textract.analyze_expense(...)` run
      synchronously inside `async def extract`, and again inside
      `async def test_connection` — which `POST /api/organization/test-extraction`
      awaits directly on the request path. Every other adapter in all 21
      registries uses `httpx.AsyncClient`, and the audit-shipping / SES / storage
      boto3 call sites all wrap in `asyncio.to_thread`. The `extract` call is
      partly covered (local-mode extraction runs on `extraction_dispatch`'s own
      worker thread); `test_connection` has no such cover.
      **Durable fix:** `await asyncio.to_thread(...)` around both, matching
      `services/storage`'s `_put_object` and round 14's SQS dispatch fix; extend
      `tests/test_sqs_dispatch_nonblocking.py`'s AST scan to cover it.
      **Trigger:** the next slice touching the extraction adapters.
### Surfaced by the round-14 frontend hunt

Six items the round-14 frontend agent traced to a file and line but did not fold
into its own tranche. Each is confirmed against the backend it disagrees with —
none is a hypothesis.

- [ ] **The same page-scoped-KPI bug this round fixed on `/expenses` is still
      live on six sibling pages.** A KPI reduces or filters over the LOADED page
      and is labelled as a whole-set figure, usually sitting beside a card that
      *is* whole-set: `/requisitions` (`periodTotal` `:112`, `pendingCount`
      `:110`, next to the server's `total`), `/budgets` (`totalAllocated` `:82`),
      `/recurring` (the monthly-run-rate reduce `:347-355` — which also divides
      floats), `/intake` (`openCount`/`reviewCount` `:87-88`), `/vendor-statements`
      (`openCount`/`totalDiscrepancies` `:283-284`) and `/positive-pay`
      (`itemsExported`/`returnsFlagged` `:252-259`). The money ones add across
      currencies too, then render the sum in `orgCurrency`.
      **Durable fix:** the shape `/expenses` now uses — a `GET …/summary` beside
      each list that shares the list's own filter builder, returns `by_status`
      counts plus per-currency exact-decimal totals, and renders through
      `utils/currencyGroups.formatCurrencyTotals`. Six small endpoints, one
      pattern; `backend/app/api/expenses.py::expense_summary` is the reference.
      **Trigger:** the next slice touching any of those pages — or one pass doing
      all six, since the sixth is the same edit as the first.

- [ ] **`GET /api/invoices/counts` ignores the list's filters, so the chips
      contradict the table.** The counts endpoint (`backend/app/api/invoices.py`
      `:236-257`) takes only `db`/`user`/`entity_id`, and
      `stores/invoices.svelte.ts:79-90` calls it with no params and never re-fires
      it on a filter change — while the list carries `search` plus eight advanced
      filters. Search "acme", get 3 rows under chips reading `All 1284 · New 402`.
      The house rule is already stated verbatim on the sibling endpoint
      (`purchase_orders.py:127-129`: "Takes the list's population filters … so the
      tallies describe exactly the rows the list would return") and `/vendors/counts`
      follows it; `/invoices/counts` is the outlier.
      **Durable fix:** give `invoice_status_counts` the same population filters as
      `list_invoices`, factored into one shared predicate builder so they cannot
      drift, and re-fire `fetchCounts` from the store's filter path.
      **Trigger:** the next slice touching the invoice list or its chips.

- [ ] **Three surfaces still label money with `orgCurrency` while the response
      states its own currency two lines away.** Now that `orgCurrency` resolves
      the *reporting* currency (this round), these read correctly far more often
      — but they are still reading the wrong source, and one is wrong outright:
      `/cfo`'s cash-position table (`:281-283`) renders `opening`/`outflow`/
      `closing` through `fmt()` while `position.opening_balance_currency` — typed
      as "the reporting currency the whole curve is denominated in" — is used only
      in the two warning banners, so the page can print "3 outflows could not be
      converted to GBP" directly above a table of `$`; `/discounts`' `aggMoney()`
      (`:93`) ignores `dashboard.currency` / `optimization.currency`, both of which
      it already renders in its guard text; and `/discounts`' per-recommendation
      card (`:379`) stamps `orgCurrency` on `rec.roi.savings`, which is computed in
      the OFFER's currency and flagged `rec.unconvertible` precisely when they
      differ (`services/discount_optimizer.py:170-172`) — the card never reads
      that flag, so "Save $412.00" can be €412.
      **Durable fix:** pass the response's own currency at each site, and render
      `rec.unconvertible` on the card rather than only in the page banner.
      **Trigger:** the next slice touching `/cfo` or `/discounts`.

- [ ] **Two backend rollups are bare cross-currency `SUM`s presented as one
      figure.** `GET /api/payments/summary`'s `total_rebates`
      (`app/api/payments.py:562-564`) is `func.sum(CardRebate.amount)` with no
      currency grouping, yet ships under the response's `"currency":
      reporting_currency` which documents itself as "what the money figures above
      are denominated in". The billing usage rollup does the same for
      `card_rebate_total` (`services/billing/usage_rollup.py:93-100`), and
      `/billing` renders it with no currency at all — `DEFAULT_CURRENCY`, so a
      GBP tenant reads `$` on that one card and `£` on every other. Distinct from
      the frontend labelling above: the *number* is wrong, not just its label.
      **Durable fix:** group by currency and convert through
      `currency_conversion` like `total_paid`/`total_pending` already do, or
      return per-currency buckets and render them side by side the way
      `formatCurrencyTotals` does elsewhere.
      **Trigger:** the next slice touching the payments summary or billing usage.

- [ ] **`/vendors/screening`'s "Payments blocked" KPI structurally cannot see a
      manually blocked vendor.** `blockedCount` (`:90`) filters `items`, which is
      the review queue — `where(Vendor.screening_status.in_(("match","review")))`
      (`app/api/vendors.py:471`). `POST /api/vendors/{id}/block` sets
      `payments_blocked = True` and never touches `screening_status`
      (`:898-900`), so a vendor AP blocked while screening-clear is invisible to a
      tally that claims to count blocked payments. (`matchCount`/`reviewCount` are
      fine — the queue is unpaginated and is exactly those two statuses.)
      **Durable fix:** count blocked vendors from a query that asks for blocked
      vendors — a `payments_blocked` tally on the counts endpoint — rather than
      from a queue selected on a different column.
      **Trigger:** the next slice touching vendor screening.

- [ ] **Four surfaces collapse loading / failed / empty into one message, and two
      error states are dead ends.** `/payments`' Runs tab (`:1234-1239`) has no
      `runsLoading` state at all, so it asserts "No payment runs yet." during the
      first fetch and forever after a failed one — while the Queue and History
      tabs in the same file do it correctly. `/catalogs` (`:285`) gates `isEmpty`
      on `!loading`, so the table renders a header with nothing under it during
      the load — no rows, no spinner, no message — and `/reports` (`:437-438`)
      has the same inversion. `/reports`' catalog failure (`:350-352`) replaces
      the entire builder with one paragraph, from a single-shot dep-free
      `$effect`, so nothing left on screen can retry it; `/experiments`
      (`:284-286`) is the same shape and renders its banner *and* "No experiments
      yet." simultaneously.
      **Durable fix:** the shape the rest of the app uses —
      `isEmpty={arr.length === 0}` with the three states composed in `empty=`
      (`routes/exceptions/+page.svelte:418` is the reference), and the
      error-with-Retry block from `routes/admin/api-keys/+page.svelte:169-172`.
      The store-side half of this landed this round for `adminStore` /
      `workflowStore`, with a guard that any store a swallowing call site cites
      must expose the flag.
      **Trigger:** the next slice touching any of those four routes.

### Surfaced by the round-14 procurement / analytics hunt

Eight items the round-14 procurement and analytics agent traced to a file and
line but did not fold into its own tranche (it landed seven fixes first). Each
is a confirmed reading of the code, not a hypothesis.

- [ ] **Adaptive approval stats mix currencies.**
      `api/adaptive_workflows._decision_rows` pulls raw `Invoice.amount` and
      feeds it to `compute_vendor_baseline` and
      `recommend_auto_approve_threshold`. Three clean JPY 100,000 vendors can
      push a recommendation to raise a USD `auto_approve_below` toward the
      $25,000 cap. The equivalent bug in the `stat_anomaly` fraud rule was
      already fixed (`test_stat_anomaly_currency_realdb.py`).
      **Why not now:** the gate it feeds (`extraction.decide_auto_approve`)
      compares raw `invoice.amount` against the same bare number, so converting
      one side alone creates a new inconsistency.
      **Durable fix:** decide what currency `auto_approve_below` is denominated
      in (reporting currency, matching `cfo_approval_above` after round 14's
      `payment_controls.cfo_approval_decision` fix), then convert both sides
      through `currency_conversion.reporting_amount_at_locked_rate`.
      **Trigger:** the next slice touching adaptive thresholds or auto-approve.

- [ ] **`GET /api/purchase-orders/{id}` leaks cross-entity invoices.**
      `api/purchase_orders.py:176` builds `linked_invoices` by joining on
      `po_number` with no `apply_entity_scope` and no vendor check —
      and `po_matching` explicitly designs around `po_number` NOT being unique
      across subsidiaries. A US-scoped viewer sees UK invoices on the PO detail.
      **Durable fix:** scope the join to the PO's own `entity_id` (shared-NULL
      union, the `vendor_matching._candidate_query` pattern).
      **Trigger:** the next slice touching purchase-order detail or 3-way match.

- [ ] **`DELETE` on intake / requisition has no status guard.**
      Deleting a converted requisition hits a RESTRICT FK → 500 rather than a
      clean 409; deleting a converted requisition that produced a PO orphans the
      PO. The intake convert route's dangling-link rebuild branch is currently
      unreachable *only because* the FK restricts — a future
      `ON DELETE SET NULL` would turn delete-then-reconvert into a double-spend.
      **Durable fix:** refuse a delete once the record has been converted (409),
      the way `DELETE /api/recurring/{id}` 409s once invoices are generated.
      **Trigger:** the next slice touching either delete route, or any migration
      changing those FK delete rules.

- [ ] **`po_matching`'s 3-way leg counts cancelled goods receipts as delivered.**
      The quantity sum ignores `GoodsReceipt.status`, so a cancelled receipt's
      quantities still satisfy the match and over-receipt is never flagged.
      **Durable fix:** filter the receipt join to non-cancelled statuses and add
      an over-receipt warning.
      **Trigger:** the next slice touching 3-way / 4-way matching.

- [ ] **Intake `vendor_id` is the only cross-object link with no existence
      validation.** An unknown UUID reaches an FK violation at flush (500
      instead of 404), and a valid *cross-entity* vendor id rides into the
      requisition and then the PO.
      **Durable fix:** resolve the vendor through the same entity-scoped lookup
      the sibling routes use before the insert.
      **Trigger:** the next slice touching intake create/update.

- [ ] **Four dashboard aggregates are each wrong in their own way.**
      `total_paid` sums `Payment.amount` across currencies where its siblings
      convert; `discount_capture` counts still-open discount windows as
      "missed" (the only one of five consumers lacking the elapsed-window gate);
      `touchless_rate` omits `sending_to_erp` / `failed` from both numerator and
      denominator; `monthly_trend` uses a 180-day window against a
      calendar-month `GROUP BY`, producing 7 buckets with a partial oldest one.
      **Durable fix:** route the money ones through
      `currency_conversion.payment_reporting_amount_sql` (as round 14 did for
      the AML trailing-spend sum), reuse the elapsed-window predicate, and
      anchor the trend window to calendar-month boundaries.
      **Trigger:** the next slice touching the dashboard KPIs.

- [ ] **`compute_fraud_rate_trend` reports "not computable" as the most
      reassuring value.** A month with zero invoices and non-zero exceptions
      returns `0.0` — a clean fraud rate — where `compute_cash_conversion_cycle`
      returns `None` for the same shape. Same class as
      [decisions §34](decisions.md)'s "cannot attest must never read as yes".
      **Durable fix:** return `None` for an empty denominator and render the
      insufficient-data state the adaptive feedback surface already has.
      **Trigger:** the next slice touching analytics trends.

- [ ] **`POST /analytics/forecast_variance` sums raw `Payment.amount` and 500s
      on a malformed month.** No `payment_reporting_amount_sql`, so the variance
      mixes currencies; and `month="2026-13"` escapes the guarded parse as a
      bare `date()` `ValueError` → 500 on user input.
      **Durable fix:** convert through the reporting-currency SQL helper and
      return 422 on an unparseable month.
      **Trigger:** the next slice touching forecast variance.

### Unverified leads from the round-14 money-path hunt — need a confirming pass

These are **hypotheses, not findings.** They were raised by exploratory
subagents during the round-14 money-path hunt and the agent that owned the area
did NOT confirm them itself, so none has a probe behind it and none should be
cited as a known defect. They are recorded because the diagnosis is the
expensive part and re-deriving the list costs more than checking it. The first
step for each is *verify or discard*, not *fix*.

- [ ] Card rebate base may be the **authorized** rather than the settled amount.
- [ ] `card_settlement_block` may ignore `expires_at`.
- [ ] Card and Positive Pay totals may mix currencies.
- [ ] The fixed-width Positive Pay formatter may truncate low-order digits and
      check numbers.
- [ ] `discounts._org_currency` may diverge from `resolve_reporting_currency`
      (round 14 fixed the frontend half of this class in
      `utils/reportingCurrency.ts`).
- [ ] The discount dashboard may sum `captured` / `missed` across currencies.
- [ ] `discount_auto_trigger` may clobber a declined offer (unlocked read, one
      commit) — note round 14 *did* fix this sweep's tier-aging bug, so the file
      has moved since the lead was raised.
- [ ] An unrecognised `screening.result` may fall through to `allow` — latent
      either way, since no shipped adapter emits one.

**Durable fix:** one scoped pass that probes each against the real DB, promotes
whatever reproduces into a fix or a `known-issues.md` entry, and deletes the
rest from this list.
**Trigger:** the next money-path hunt, or the next slice touching cards,
Positive Pay, or discounting.


### Surfaced by the round-15 bug hunt

Each of these was found while fixing something else, was judged out of the
fixing agent's file scope, or could not be proven reachable — so it was reported
rather than patched. None is a diagnosed defect (those go to
[known-issues.md](known-issues.md), which is currently empty).

- **Vendor enrichment is not entity-scoped.** `POST /api/enrichment/vendors/{id}/enrich`
  and `.../apply` take `get_entity_id` purely as a tenant chokepoint
  (`# noqa: ARG001`) and never scope the `Vendor` lookup, so in a multi-entity
  tenant a steward on subsidiary A can enrich and apply firmographics onto
  subsidiary B's vendor — including `name`, which the apply path re-screens.
  Same-tenant only, so no cross-tenant exposure. **Durable fix:** wrap both
  lookups in `apply_entity_scope(select(Vendor)…, include_shared=True)`, matching
  `vendor_matching._candidate_query`, and add an entity-isolation case to
  `tests/test_enrichment_apply.py`. **Trigger:** the next change touching
  `app/api/enrichment.py`, or the next multi-entity audit pass.

- **GL account codes have no uniqueness, and the ERP sync upsert ignores entity.**
  `POST /api/gl-accounts` performs no duplicate check and `gl_accounts` carries no
  unique constraint on `(organization_id, code)`, so the same code can be created
  twice; and `POST /api/gl-accounts/sync-erp` matches existing accounts on
  `(code, organization_id)` **without** the entity filter, so in a multi-entity
  tenant a sync run while entity B is selected updates entity A's row rather than
  creating B's — contradicting the route's own docstring ("same rule as manual
  create"). **Durable fix:** make the sync upsert key entity-aware (shared ∪
  selected entity, mirroring `gl_recode._ActiveChart.is_valid_for`) plus a partial
  unique index over `(organization_id, entity_id, code)` in a fanned-out
  migration. **Trigger:** the next change to the chart-of-accounts write path, or
  the first multi-entity tenant running an ERP chart sync per subsidiary.

- **Corporate-card unmatch leaves a stale `Expense.payment_method`.**
  `api/expense_cards.py::unmatch_card_transaction` clears both FK legs but never
  restores the payment method `_link_both_sides` stamped, so an expense
  mis-matched to a card reads `corporate_card` / `virtual_card` forever. Resetting
  to `out_of_pocket` is *not* the fix — an expense can legitimately be card-marked
  before its feed row is imported, so that would be a different wrong guess.
  **Durable fix:** record the pre-match value (a nullable
  `payment_method_before_match` column, migration) and restore it on unmatch.
  Fold in `create_expense_from_card`, which mints an expense without calling
  `_refresh_policy_violations`, so a card-derived line carries no policy flags
  until its next PATCH. **Trigger:** the next change that makes
  `Expense.payment_method` load-bearing beyond `services/report_builder`
  reporting — e.g. a reimbursement run that skips card-funded lines.

- **Exception-agent resolvers call the max-amount cap by the CFO gate's name.**
  `services/exception_agents/resolvers/{amount_mismatch,gl_coding,missing_po,multi_po_split}.py`
  evaluate `max_invoice_amount` with `cfo_gate_applies(max_amount, amount)`. The
  contract is identical and fail-closed, so behaviour is correct — but on a
  malformed threshold it logs *"requiring human (CFO) approval"* for a **max cap**
  trip, pointing an operator at the wrong setting. **Durable fix:** swap the four
  call sites to `approval_chain.max_amount_gate_applies` (added 2026-08-21, same
  shared `_money_gate_applies` body). **Trigger:** the next change touching those
  resolvers.

- **The card family's local-first story is weaker than its siblings'.**
  `card_adapters/dispatcher.REGION_DEFAULTS` resolves an *unset* provider to
  `lithic`, not `mock`, and `scripts/seed.py` seeds every demo tenant with
  `cards.enabled: true` and no provider — so a fresh clone's
  `POST /api/cards/generate` reaches for a real issuer, which guard rail 7 says
  it should not. Pre-existing and untouched by the round-15 refusal work (that
  only fires on a *named* unknown provider, and deliberately left the unset path
  alone). **Durable fix:** default an unset provider to `mock` like the other two
  registries, and make `REGION_DEFAULTS` a preference applied only once a real
  provider is configured — or seed the demo tenants with an explicit
  `cards.provider: "mock"`. **Trigger:** the next change to card provider
  resolution, or the first contributor surprised by a fresh clone calling out.

- **A same-currency expense report can be submitted with a NULL reporting total.**
  Round 15 taught the *line*-level lock (`_lock_line_conversion`) to resolve the
  currency pair before demanding an FX adapter, so a same-currency line locks at
  rate 1 for a tenant whose `settings.fx.provider` names no registered adapter.
  The *report*-level lock was not given the same treatment, so such a tenant
  submits with `reporting_amount` NULL and `reporting_total: null` in the audit
  row. **Not a control failure** — `expense_currency.report_amount_for_gate`
  falls back to `total_amount` when the report currency equals the reporting
  currency, so the CFO gate still evaluates the right figure — it is a
  completeness gap in the stored snapshot. **Durable fix:** mirror the
  pair-first check at the report level. **Trigger:** the next change to expense
  report submission, or the first report of a null `reporting_total`.

- **`GET /api/experiments/{id}/results` would 500 on a non-object `audit_log.details`.**
  `api/workflow_experiments._experiment_metric_rows` does
  `dec["details"].get("changes")` after `details = details or {}`, so a `details`
  that is a list or scalar raises `AttributeError` and loses the whole readout.
  **Deliberately not fixed:** the raising expression was proven, but no real write
  path can put a non-dict there — every writer passes a dict — and manufacturing a
  fix for an unreachable state is how speculative complexity gets in. It is the
  same *direct-DB-tamper* shape `services/approval_signature.check_approval_row`
  already absorbs by counting the row rather than failing the period. **Durable
  fix:** treat a non-dict `details` as "no changes". **Trigger:** the next change
  to the experiment results readout, or the first report of a 500 there.

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
