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

**Last reconciled:** 2026-08-16 against `improve/round-batch-3` — a three-agent
round that closed **every remaining actionable `(c)` item**. What is left in
this file is the `(a)` credential-blocked set, the `(b)` operator steps, and two
`(c)` entries that are product calls rather than work (an unwired adapter family
and the copilot's saved-plans bucket).

A later backend round added one more `(c)` entry — eight built-and-documented
capabilities with no production caller — found while closing the
adapter-registry defect behind [decisions §28](decisions.md). That round did
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

### Built-and-documented backend capabilities with no production caller

Surfaced by the survey behind [decisions §28](decisions.md) (which closed the
adapter-registry half of the same sweep: all three money-touching dispatchers
now fail closed on an unrecognised provider name instead of silently resolving
to their fixture adapter). Each item below is code that exists, is tested, and
that nothing in `app/`, `scripts/` or `alembic/` reaches:

- [ ] **`expense_policy.mileage_reimbursement` is never called.** The whole
      stack around it is wired — `Expense.mileage_miles`,
      `ExpensePolicy.mileage_rate`, both schemas, both CRUD paths — but
      `evaluate_expense` never looks at mileage, so an admin sets `$0.67/mile`,
      an employee logs 120 miles, and the reimbursable amount is whatever
      free-text `amount` they typed. `expense-management.md` labels the rate
      "*Defined in WF1; enforced in WF3*"; WF3 never landed the enforcement.
      **Durable fix:** compute it at expense create/update and either surface
      it on the response or raise a `mileage_amount_mismatch` violation from
      `evaluate_expense`. No migration — the columns exist.
- [ ] **Teams interactive approval is inbound-only.**
      `email_action_token.build_teams_action_tokens` has no production caller;
      `notification_dispatch._build_slack_action_tokens` hard-returns
      `(None, None)` unless the provider is literally `"slack"`; and
      `chat_notification_adapters/teams_adapter.build_body` emits an `OpenUri`
      MessageCard that never reads `ChatMessage.approve_token` /
      `.reject_token`. Meanwhile the entire inbound half ships and is mounted
      (`api/teams_approvals.py` — HMAC verify, replay window, channel-bound
      single-use `jti`). A Teams org gets a read-only card and a
      security-reviewed public endpoint nothing can reach.
      **Durable fix:** dispatch `_build_slack_action_tokens` on the provider
      and add the `potentialAction` / `Action.Http` block to `build_body`.
      Already named in `backend/docs/teams-approval.md` § Deferred.
- [ ] **Sanctions `ScreeningResult.categories` / `.adverse_media` are computed
      and then dropped.** `refinitiv` maps World-Check `ADVERSE-MEDIA` into
      them and `mock` simulates it, but all three consumers
      (`compliance.check_payment_compliance`,
      `vendor_screening.screen_vendor_record`, `vendor_risk_scoring`) read only
      `.result` / `.risk_score` / `.matched_list`. A negative-news hit — the
      thing the taxonomy was added for — never reaches the compliance verdict
      reasons, the vendor's `risk_factors`, or the persisted `SanctionsCheck`.
      **Durable fix:** fold `categories` into `SanctionsCheck.raw_response` and
      `Vendor.risk_factors` (both JSONB, no migration) and add an adverse-media
      reason to `ComplianceDecision.reasons`.
- [ ] **`international_payments.is_international_payment` is dead while three
      live modules hand-roll its rail set** — `payment_corridor`
      (`_EXPLICIT_INTERNATIONAL_METHODS`), `compliance`
      (`_DEFAULT_HIGH_RISK_METHODS`, which drives KYC thresholds) and an inline
      literal in `api/payments.py` that decides whether an FX rate is locked at
      all. Adding a fourth international rail means remembering all three; the
      canonical predicate that would prevent the drift is the unused one.
      **Durable fix:** export the frozenset and have the three sites import it.
- [ ] **`workflow_engine.is_known_step_type` is dead and `BUILDER_STEP_TYPES`
      is forked** — defined identically in `workflow_engine` and
      `workflow_builder` with no cross-check. The builder validates against its
      copy; the engine validates against nothing and resolves step numbers via
      `STEP_TYPES.index(resolved)`, so a builder step type reaching
      `create_workflow_step` raises `ValueError: 'condition' is not in list`
      rather than being recognised — which is exactly what the unused helper
      was written to prevent.
      **Durable fix:** one source of truth for the list, call the helper at the
      definition-save chokepoint, guard the `.index()` calls.
- [ ] **`data_residency.check_residency_alignment` is dead**, so
      `GET /api/organization/data-residency` cannot report that a tenant pinned
      to `eu` is being served from a single-region platform. There is no
      `deployed_region` setting to compare against either.
      **Durable fix:** add the env-backed setting and surface `aligned` on the
      response (advisory, never blocking — which is what the function already
      does). No migration.
- [ ] **`analytics.compute_dpo_trend` is dead and re-derived inline twice**
      (`api/analytics.py` in two places). The second copy emits `float(ap)` /
      `float(cogs)` / `float(dpo_val)` — money crossing the API boundary as
      float, against the Decimal invariant, in the path the canonical helper
      would have kept exact.
      **Durable fix:** call the helper from both sites.
- [ ] **`tax_rate_adapters/{avalara,taxjar}.test_connection` return `True` on
      credentials alone** while their `get_rate` unconditionally raises
      `NotImplementedError` — a fabricated healthy probe for an adapter that
      can never satisfy its contract's core method. Impact is currently nil
      (nothing calls `test_connection` on that family), which is why it sits
      below the rest.
      **Durable fix:** return `False` from the skeleton probes, matching the
      `qms_adapters/generic_qms` posture.

**Why deferred:** each is a genuine gap but a *different* feature's wiring, and
landing eight unrelated behaviour changes behind one round's regression
envelope is how a fix becomes indistinguishable from a regression. None is
blocked on anything.
**Trigger:** the next round touching that feature — or take them as a batch;
they are individually small.

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

### The axe route list still trails the routes that carry dialogs

Widening the guard to `/admin` + `/vendor-statements` immediately caught a real
`serious` contrast failure (`--text-muted` on the newly-defined `--surface-2`,
4.34:1). Fixing it turned up the **same** defect on `/billing`'s proration box —
found by reading, not by the guard, because `/billing` is not in the route list.
That is the coverage gap restating itself: the pages with one-time secret
reveals, saved-card metadata and armed destructive actions are exactly the ones
worth guarding, and `/billing`, `/reports`, `/experiments` and `/cfo` are still
outside it.

- [ ] Extend `frontend/tests-e2e/a11y/axe.spec.ts` to `/billing`, `/reports`,
      `/experiments` and `/cfo`. Expect them to pass — they reuse the shared
      `ui/` primitives — and treat any failure as the finding, fixing the root
      rather than relaxing the rule.
- [ ] Consider whether a token-pairing guard (which foreground tokens are legal
      on which surface tokens) beats route-by-route coverage for this class. The
      contrast bug recurs per *surface*, not per route, so a lint over
      `app.css` + component styles would catch it everywhere at once.

**Why deferred:** the routes were added as part of closing the previous a11y
gap, and each newly-guarded route is a potential new CI failure. Adding four
more unverified routes in the same change that was fixing a red build would
have made a second failure indistinguishable from a regression in the fix.
**Trigger:** the next `/audit:accessibility` pass, or any change to a page in
the list above.
Ref: [accessibility.md](accessibility.md).

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
