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

**Last reconciled:** 2026-09-04 (second pass) — a five-agent coverage pass over
the work #321 records as complete, run after PR #356 merged (`71231ee3`). It
added ~630 tests across nine files, and **found four real defects that the
shipped code's own tests had not**, each fixed at the root here with a
regression test that fails against the previous implementation:

| Defect | Where | Why the existing tests missed it |
|---|---|---|
| The card dashboard's rebate rollups were org-wide while its card figures were entity-scoped | `api/cards.card_dashboard` | `CardRebate` carries no `entity_id`; the `VirtualCard` join that makes the subsidiary reachable only arrived with #356's currency fix, and `GET /rebates` had been scoping its list that way all along — so the two disagreed and nothing compared them |
| `looks_like_email` admitted NUL / ESC / DEL and the rest of the C0 range | `utils/emails` | The shape pattern's classes are built on `\s`, which excludes only whitespace. CR and LF *were* refused, so §50's header-injection fix looked complete; the wider class §60 introduced `is_header_safe` for was reachable at signup, partner child-provisioning and the scheduled-report recipient list, none of which call the header rule |
| `GET /api/payments/counts` declared no filters and tallied the whole set | `api/payments` | It ends in `/counts`, not `/summary`, so the OpenAPI-driven rollup guard never discovered it. The frontend sends a live `search` on the list and nothing here, so a one-vendor search left the chips reading the tenant's total over a one-row table — the #352 defect on a sibling surface |
| `GET /api/vendors/counts` hand-rolled the search predicate and dropped `source` | `api/vendors` | The search columns coincided with the shared builder's, so nothing was visibly wrong; `source` was a live undercount waiting for the vendors page to gain that control |

Two prose corrections went with them: the recurring monthly-equivalent is
quantised **once per currency over exact quotients**, not per template (the
values differ — three 100.00 annual templates are 25.00 a month, not 3 × 8.33 =
24.99), and the roadmap section counts were off by one after the Cash-Flow
Copilot section shipped into the archive (44 shipped / 7 open, not 43 / 8).

The pass also extracted the discount partial-realised-set rule out of the page
template into `utils/discountPartialSet.ts` so it could be unit-tested without
the test restating its own sum — which closed a small robustness gap, since a
negative count off the wire used to cancel a genuine exclusion and hide the
banner.

**Also reconciled 2026-09-04 (first pass)** — the confirming pass the
"unverified leads" section had been waiting for. All **eight** round-14 money-path leads were
probed against the real code; **all eight reproduced**, so none was discarded
and the whole section is gone: they are findings now, fixed with regression
tests that fail against the previous implementation. What they were:

| Lead | Verdict |
|---|---|
| Card rebate base is authorized, not settled | Real — the settled branch ignored the settlement event's own `amount`, and the `or amount_limit` fallback rebated on the card's authorization CEILING (a $10,000 card settling $100 earned a rebate on $10,000). Now `resolve_rebate_base`, which cannot reach the limit because it is not passed one |
| `card_settlement_block` ignores `expires_at` | Real — an aged-out card was a valid settlement target, so the payment went `completed` and the invoice `paid` while the vendor was never paid at all |
| Card totals mix currencies | Real — bare cross-currency `SUM`s over `amount_limit` / `amount_charged` / `CardRebate.amount` under no currency code at all. `CardRebate` has no currency column, so the rollups now join `VirtualCard` |
| Fixed-width Positive Pay truncates | Real — and worse than "loses precision": the pad-then-slice kept HIGH-order digits, rescaling an overrunning amount by ten per dropped digit. The drawee account column was also 8 chars against a FULL account number, so every file carried a truncated account |
| `discounts._org_currency` diverges | Real — one settings key plus a hardcoded `"USD"` against the canonical four-step chain |
| Discount dashboard sums across currencies | Real — `captured` / `missed` unfiltered while `projected_savings` in the same response was filtered |
| `discount_auto_trigger` clobbers a decline | Real — unlocked read, no status predicate on the update, one commit; a committed decline was overwritten and an append-only audit row asserted the sweep found the offer open |
| Unrecognised `screening.result` → `allow` | Real — only `match` / `review_required` branched, so a fourth value fell through to the trailing `allow` ([decisions.md](decisions.md) §61) |

The Positive Pay half of that third lead ("Card **and Positive Pay** totals may
mix currencies") is closed differently: `api/positive_pay` already resolves the
stored row's currency through `resolve_reporting_currency`, and its two
`invoice_defaults.currency` reads are the FormatterContext for the rendered
file, not a rollup — no cross-currency sum exists there to fix.

This file now carries **57** open checkbox entries (plus the 8 narrative
round-15 findings) — **65** items. The money-path pass took the checkbox count
64 → 56; the coverage pass that followed it added one back (the `float` money
filter bounds, below), which is the file working as intended: a sweep that
closes nothing and opens nothing has usually not looked hard enough.

The same pass replaced the list/rollup filter-parity guard with
`test_whole_set_kpi_rollups.py`. The old one covered four surfaces and only
`search`, through an `isinstance(default, fastapi.params.Query)` check that a
plain-default parameter is not — so it passed vacuously on `/api/recurring`,
which is written that way. The replacement discovers surfaces from the mounted
OpenAPI schema, checks the full filter set, and asserts both endpoints route
through the shared `_*_list_filters` builder. Discovery turned up two more
rollups taking none of their list's filters — `/api/payments/summary` and
`/api/exceptions/summary` — both deliberate (a whole-entity treasury figure;
chip counts that must span every value) and both called from the frontend with
no parameters, so they are recorded as justified exemptions in the test rather
than dropped, and a stale exemption fails.

**Previously reconciled:** 2026-09-03 — a coverage pass over the work issue
[#321](https://github.com/Absence0760/project-account-payables/issues/321) said
was complete, which closed three entries and opened none. `GET
/api/expenses/export` gained the list's filter builder; the six unguarded
`datetime.now(UTC).date()` sites converged on `utc_today` behind a whole-`app/`
scan; the badge conversion gained a ratchet (`badgeAudit.test.ts`). Writing the
call-site tests for the round-13 email hoist surfaced one new defect, fixed in
the same pass:  `identity_provisioning.extract_and_check_email` stored an
IdP-supplied address containing a control character as `User.email`
([decisions.md](decisions.md) §60). The GitHub mirror (#321), four rounds stale,
was re-synced.

**Previously reconciled:** 2026-08-20 against round 14 — a five-agent parallel bug
hunt across the money path, auth/tenant isolation, the SvelteKit frontend, the
background sweeps and adapter registries, and procurement/analytics. Twenty-nine
defects were fixed and committed with regression tests. The findings each agent
verified in the source but correctly did not fold in are recorded below, one
subsection per area, immediately above § (a).

**Before that:** 2026-08-19 against round 13 — a four-agent sweep of the
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

The last of it is **closed** too. The six modules that inlined
`datetime.now(UTC).date()` — `api/api_keys`, `api/bank_reconciliation`, the
recurring / contract-renewal / discount-auto-capture sweeps and the mock
financing adapter, plus `api/cash_flow` and the four copilot tools that
predated the helper — now import `utc_today`, and the guard stopped being an
opt-in allowlist: it AST-scans **the whole of `app/`** for a local-timezone
"today", and separately fails on an inlined `datetime.now(UTC).date()` outside
`utils/dates.py`. The allowlist was the right shape while the tree was mixed
and the wrong one once it wasn't — a list cannot see a module nobody added to
it, and a new module is where the next `date.today()` arrives.

### Tinted badges — the shared primitive exists, half the call sites still hand-roll

The contrast half of this entry is long closed (the 29 badges below 4.5:1, fixed
via tint-paired text tokens — [decisions.md](decisions.md) §30). Round 12 closed
the **ownership** half: `frontend/src/lib/components/ui/Badge.svelte` is now the
single owner of the tinted-badge recipe. A caller names a *tone* and cannot spell
it wrong; `variant` passes the caller's semantic class through as a **selector
hook only** (the e2e suite reads `.badge.approved`), never as colour. Rationale,
including why sizing is fixed rather than a prop and why `neutral` / `erp` stay
non-tinted: [decisions.md](decisions.md) §47.

- [ ] **The badge conversion has a ratchet now; the remainder is the tranches.**
      `frontend/src/lib/a11y/badgeAudit.test.ts` scans every stylesheet for a
      badge-shaped selector (`badge` / `chip` / `pill` / `tag`) that sets both a
      tinted background and a colour, holds each file to a recorded baseline and
      holds the converted files (`/payments`, `/admin/webhooks`,
      `RequisitionModal`, `ExpenseModal`, and now the dashboard) at zero. **The
      baseline map is the live count** — no number is restated here or in
      `frontend/CLAUDE.md`, both of which went stale within one round last time.
      The audit deliberately counts more than the seven files this entry used to
      name: `chip` / `pill` / `tag` are the same capsule under other names, and
      the recipe is respelled in components (`RunDetailModal`, `ScreeningBadge`,
      `SupplierChatThread`) as readily as in routes.
      **Why still staged:** the tokens standardise on alpha `.15`, so converting
      a `.1` or `.12` rule *visibly* strengthens that badge. Landing them all at
      once would make any visual complaint unattributable.
      **No distinction was lost** in the tranches so far. `pending_compliance`'s
      ring — the thing that says "a human must clear this", where the tone alone
      says only "waiting" — was preserved as a caller-owned wrapper rather than
      flattened or turned into a one-caller prop ([decisions.md](decisions.md)
      §52). Every other consolidation merged partners that had **no rule at all**
      and rendered untinted. Two chips stay off the primitive by design
      (`.discount-chip` is two stacked lines; `.blocked-chip` wraps a localised
      sentence where `nowrap` would break 320px reflow); both took the tokens.
      The dashboard's duplicate `.overdue-badge` — the same flag rendering at two
      sizes on two pages — is **closed**.
      **Durable fix:** convert the rest in attributable tranches, checking
      collapsed distinctions as you go, editing the baseline down in the same
      commit, and hoisting the two tone maps out of the modals into
      `types/{requisition,expense}.ts` when their files convert.
      **Trigger:** the next slice touching any file the baseline names.

### Surfaced by the round-13 sweep

Three items the round-13 agents confirmed but correctly did not fold into their
own tranches. (The fourth — `GET /api/expenses/export` having no `search` leg —
is **closed**: the export now runs `status` / `report_id` / `search` through the
same `_expense_list_filters` as the list and the KPI rollup, and the CSV button
sends the term it is filtered by.)

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

- [x] **DONE (PR #349).** The same page-scoped-KPI bug `/expenses` fixed was
      live on six sibling pages — a KPI reducing or filtering over the LOADED
      page while labelled whole-set, usually beside a card that *is* whole-set:
      `/requisitions` (`periodTotal`, `pendingCount`), `/budgets`
      (`totalAllocated`), `/recurring` (the monthly-run-rate reduce, which also
      divided floats), `/intake` (`openCount`/`reviewCount`),
      `/vendor-statements` (`openCount`/`totalDiscrepancies`) and `/positive-pay`
      (`itemsExported`/`returnsFlagged`); the money ones added across currencies.
      Each now has a `GET …/summary` endpoint sharing the list's own filter
      builder (`_<x>_list_filters`), returning whole-set `by_status` counts +
      per-currency exact-decimal totals (grouped, never a cross-currency SUM,
      never FX-converted on a read), rendered through
      `utils/currencyGroups.formatCurrencyTotals`. `/recurring`'s monthly
      normalisation moved to exact Postgres numeric + `ROUND_HALF_UP`.
      `backend/app/api/expenses.py::expense_summary` was the reference.

- [x] **DONE (PR #351).** `GET /api/invoices/counts` ignored the list's filters,
      so the chips contradicted the table — search "acme", get 3 rows under chips
      reading `All 1284 · New 402`. `invoice_counts` now takes the list's
      population filters (`search` + the advanced filters + `assigned_to_id`)
      through the **same** `_invoice_list_filters` builder as `list_invoices`
      (with `status=None` — status is the dimension being tallied), and the
      `/invoices` page re-fires `invoiceStore.fetchCounts(buildParams())` from
      the filter effect + the debounced search (the store gained its own
      `countsSequence` so a stale tally can't land over a fresh one). Matches the
      rule already stated on `purchase_orders.py::purchase_order_status_counts`
      and `/vendors/counts`.

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

### Surfaced by the #321 coverage pass (2026-09-04)

The pass fixed four defects at the root (recorded in the reconciliation header
above) and its review round closed six more quality findings in the same
branch. One item is left open, because fixing it only where it was noticed
would make the codebase less consistent rather than more.

- [ ] **Money filter bounds on list endpoints are typed `float`, not `Decimal`.**
      `amount_min` / `amount_max` are declared `float | None` and then converted
      with `Decimal(str(value))` — in `api/payments.py` (`_payment_list_filters`,
      the list, and now `/counts`) and `api/invoices.py`
      (`_invoice_list_filters`), i.e. it is the house pattern rather than one
      site's slip. `Decimal(str(f))` recovers the shortest repr, so ordinary
      inputs round-trip, but a bound given to more precision than a float holds
      is silently re-rounded before it reaches a `Numeric` column — so a payment
      sitting exactly on the boundary can fall the wrong side of the filter.
      These are query bounds rather than stored amounts, which is why it has not
      bitten, but root `CLAUDE.md` § Project invariants states the money rule
      without that carve-out.
      **Durable fix:** type the params `Decimal | None` (pydantic/FastAPI parse
      it natively) and drop the `Decimal(str(...))` hop, on **both** sides of
      each shared filter builder at once.
      **Why not folded into the coverage pass:** the two sides of a builder must
      move together or the list and its rollup filter differently — and doing it
      for payments alone would leave invoices (and any sibling that follows the
      same pattern) on the old shape, which is the drift the shared builders
      exist to prevent. It wants one sweep across every list surface that takes
      a money bound.
      **Trigger:** the next change touching a money filter bound on any list
      endpoint, or the next money-exactness audit pass.

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

- **e2e specs still hand-roll invoice teardown.** `invoices` is referenced by 16
  foreign keys and none cascade, so a bare `DELETE FROM invoices WHERE ...` only
  works while the invoice happens to have no children. Round 15 added
  `tests-e2e/fixtures/helpers.ts::deleteInvoicesWhere`, which owns the full child
  graph, and moved the one spec that broke (`upload-refetch-failure`, which
  started failing teardown the moment extraction began succeeding and writing
  line items). About 19 other specs still delete invoices directly. They pass
  today because the invoices they create never acquire the children in question —
  which is exactly the implicit dependency that just bit. **Durable fix:** move
  the remaining call sites onto `deleteInvoicesWhere`. **Trigger:** the next e2e
  teardown failure on a foreign-key violation, or the next spec added that runs a
  real extraction.

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

### Surfaced by the persona-panel round-2 parallel fix batch (issue #328)

- **`pnpm i` dropping the frontend's security-pin overrides — SUPERSEDED.**
  This entry described pnpm 11 no longer reading `pnpm.overrides` from
  `frontend/package.json` (where `cookie@<0.7.0` and `undici@<7.28.0` are
  pinned), so a plain `pnpm i` regenerated the lockfile with the CVE pins
  silently gone. Its proposed fix was a `frontend/pnpm-workspace.yaml`.
  **A different fix landed** in #353: both `package.json` files now pin
  `packageManager: pnpm@10.12.4` and every `pnpm/action-setup` site reads it
  instead of passing `version:`, so one pnpm — a 10.x that does read that
  location — writes the lockfile everywhere. The pin is the thing to preserve;
  moving the overrides is only needed if the project later moves to pnpm 11+.
  What remains open is the *verification*, tracked below as
  § Surfaced while clearing the open-PR backlog → "Confirm the `packageManager`
  pin stopped Dependabot dropping the pnpm overrides", which is where the recipe
  and the recurrence instructions live. Kept as a pointer rather than deleted:
  the diagnosis (which pnpm versions read which location) is the expensive half.

### Surfaced by the issue #328 checklist reconciliation (2026-08-27)

Going through all 56 persona-panel findings + 8 acknowledged gaps against `main`
after PRs #329, #330 and #341 landed left **13 findings genuinely open** plus
**8 product-fit gaps awaiting a keep-or-drop call**. They are parked here so
issue #328 can close — the checklist itself is not a destination (guard rail 6).
Every one is category **(c)**: a sized-but-unstarted piece of work, or a
deferred-with-reason finding awaiting a product/architecture call.

**Progress — PR #343** (`feat/portal-invoice-search-filter`):

| #328 finding | Status in #343 |
|---|---|
| Portal invoice list — no status/number filter | **done** — repeatable `status=` + `search=`, vendor-facing phase chips |
| Portal payment list — no status/number filter | **done** — same, via shared `PortalListFilters.svelte` |
| Vendor can't see why an invoice was rejected | **done** — `rejection_reason` on the portal API + rendered under the status pill |
| No resubmit path for a rejected portal invoice | **done** — `POST /portal/invoices/{id}/resubmit` + "Revise & resubmit" row control |
| URL filter/search persistence partial on `/invoices` `/payments` `/vendors` | **done** — `search` + status chip + (payments) tab now in the query string |
| No onboarding empty-state / CTA for a zero-data tenant | **done** — shared `ui/EmptyState.svelte`, adopted on the dashboard, `/invoices`, and `/portal/invoices` |
| No UI to create a vendor / invite one to the supplier portal | **done** — `+ New Vendor` header action (`CreateVendorModal`) + `Invite` row action (`InviteVendorPortalUserModal` → `SecretReveal`) |
| `/payments/queue` has no pagination | **done** — `?page=` on `GET /queue`, a `GET /queue/ids` select-all resolver, Load-More + whole-set select-all on the Queue tab |
| GBP→GB domestic payment falls through to `international_wire` | **done** — `bacs`/`faster_payments`/`chaps` rails + a GBP/GB branch in `pick_corridor` (Faster Payments, no SWIFT/FX/IBAN) |
| [Low] Org Settings has no first-time-admin prioritization | **done** — a "Getting started" wayfinding strip at the top of `/organization` |
| Portal lists have no date-range filter | **done** — `date_from`/`date_to` on both portal list endpoints + a From/To pair in `PortalListFilters` |
| Vendor can't see why an invoice is *stuck* | **done** — `waiting_on` bucket (`review`/`processing`/`erp`) + `waiting_on_days` on the portal invoice API, rendered under the status pill |
| _remainders_ | constrained re-extract on resubmit (scoped, deferred — its own slice) — entry below |

**Frontend gaps — built on the backend, unreachable in the product:**

- [x] **UI to create a vendor + invite one to the supplier portal — DONE
      (PR #343).** `+ New Vendor` header action (`vendor.manage`-gated) opens
      `CreateVendorModal` (`POST /api/vendors`; no bank field — the backend
      dual-control-stages that on create); an `Invite` row action
      (`auth.isManager`) opens `InviteVendorPortalUserModal`
      (`POST /api/vendors/{id}/portal-users`) whose one-time temp password is
      shown via the shared `SecretReveal`. Guard:
      `tests-e2e/vendors/create-invite.spec.ts`.

- [x] **No onboarding empty-state / CTA for a zero-data tenant — DONE
      (PR #343).** `ui/EmptyState.svelte` (icon + heading + description +
      optional button/link action, i18n-agnostic) is adopted on the dashboard
      (zero invoices → `/invoices`), `/invoices` (zero rows + no filter → the
      upload action, role-gated), `/portal/invoices` (vendor submitted nothing
      → the submit action), and `/vendors` (zero vendors + no filter → the
      `+ New Vendor` action, `vendor.manage`-gated — gated on a first-fetch
      `loaded` flag so it doesn't flash during load, and on `!loadErrored`).
      Each page keeps its `DataTable` + loading/errored/filtered-empty copy
      for every other state. Guards:
      `tests-e2e/reactivity/empty-state.spec.ts` (`/invoices` + `/vendors`).

- [x] **[Low] Organization/Settings first-time-admin prioritization — DONE
      (PR #343).** A "Getting started" wayfinding strip at the top of
      `/organization` links a new admin to the five sections they configure
      first (Company Profile, Invoice Defaults, Users & Roles, approval
      thresholds, Branding). Sections are neither reordered nor hidden — it's a
      shortcut strip with anchor `id=`s. Guard:
      `tests-e2e/organization/getting-started.spec.ts`.

- [ ] **[Low] The marketing pricing page (`Pricing.svelte`) is USD-only.**
      Hardcoded `$` figures; no currency awareness.
      **Durable fix:** a product call on whether to localise pricing at all, then
      per-locale figures if yes.
      **Trigger:** an international pricing decision.

**Supplier portal — the loop-closing steps are missing:**

- [x] **A vendor can see why an invoice is *stuck* — DONE (PR #343).** The
      *rejected* half (`rejection_reason`) shipped earlier in the PR;
      `GET /portal/invoices[/{id}]` now also carries `waiting_on` — a PII-free
      bucket (`review` / `processing` / `erp`) plus `waiting_on_days`, set
      **only** while the invoice is in a processing phase, NULL for
      `new`/`approved`/`paid`/`rejected`/`done`. Never an internal status
      string or a user name. Rendered as a localized line under the status
      pill ("Awaiting your customer's review · 5 days"). Guard:
      `tests/test_portal_waiting_on.py`. (A finer step-level detail off the
      workflow instance was scoped down to the phase-bucket + age — enough to
      add information beyond the chip without touching workflow internals.)

- [ ] **A resubmitted portal invoice isn't re-extracted from the corrected
      document.** The resubmit path is **shipped** (**PR #343**) —
      `POST /portal/invoices/{id}/resubmit` swaps the file on the same row (no
      duplicate flag), resolves the `review_rejected` exception, and sends it
      back to `ready_for_review`. It deliberately does **not** re-run
      extraction, because a fresh pass calls `match_and_link_vendor` and can
      re-link `Invoice.vendor_id` to a different supplier — which would drop
      the invoice out of the `vendor_id ==`-scoped portal list, i.e. the
      vendor loses sight of their own resubmission. So the AP reviewer has to
      manually reconcile the new PDF against the (stale) extracted fields.
      **Durable fix (scoped, not started):** thread a `skip_vendor_match: bool`
      through `services/extraction_dispatch.dispatch_extraction` → the queue
      tuple / lambda payload → `services/extraction.run_extraction`, guarding
      the `match_and_link_vendor` call (`extraction.py` ~L590) so the resubmit
      path can re-extract money/number fields while keeping the existing vendor
      link. **Left deferred deliberately:** it changes the extraction dispatch
      path in both local and lambda modes — a shared, money-adjacent surface
      that warrants its own focused slice + tests, not a tail-end add to this
      PR.
      **Trigger:** the next slice touching portal resubmit or the extraction
      dispatch path.

**Volume surfaces:**

- [x] **`GET /api/payments/queue` pagination — DONE (PR #343).** `?page=` /
      `?page_size=` on `GET /queue` (order `due_date ASC NULLS LAST, id ASC` —
      the `id` tie-breaker the invoice list has), plus a `GET /queue/ids`
      resolver for the whole selectable set (capped, currency-bucketed). The
      Queue tab renders Load-More; "select all N matching" resolves via
      `/queue/ids` (not the loaded rows) and the pay-bar count / per-currency
      subtotals / mixed-currency guard derive from the whole-set rollup.
      Guards: `tests/test_payment_queue_pagination.py`,
      `tests-e2e/payments/queue-pagination.spec.ts`.

- [x] **URL filter/search persistence on `/invoices`, `/payments`, `/vendors`
      — DONE (PR #343).** Each page now initialises `search` + the status chip
      from the query string and folds them into its `syncUrl()` writer
      (untracked, called from the filter effect + the debounce timer);
      `/payments` also persists the active tab. Guard:
      `tests-e2e/reactivity/filter-url-persistence.spec.ts`; the debounce it
      sits next to stays covered by `search-debounce-race.spec.ts`.

- [ ] **Budgets "Total Allocated" KPI** — already tracked in
      _§ Surfaced by the round-14 frontend hunt_ ("the same page-scoped-KPI bug …
      still live on six sibling pages", `/budgets` `totalAllocated` `:82`). Same
      fix, same trigger; not re-filed here.

**UK / tax — needs its own design session, not a patch:**

- [ ] **The invoice tax model has no rate category or reverse-charge flag, and
      UK domestic (same-country) VAT reverse charge is structurally
      impossible.** `Invoice`/`InvoiceLineItem` carry one flat
      `tax_amount`/`tax_rate`; `international_tax/vat.py` hardcodes domestic
      reverse charge to `False` and models GB as non-EU, so the UK CIS domestic
      reverse charge can never be expressed, and the `/api/international-tax`
      calculator is never wired to a real invoice.
      **Durable fix:** a tax-treatment design session — per-line rate category +
      a reverse-charge flag on the line, the calculator wired into the invoice
      lifecycle, and a real frontend for it. Explicitly scoped out of the
      parallel bug-fix rounds as architecture, not a fix.
      **Trigger:** a decision to support UK/EU VAT properly (a prerequisite for
      the UK-business go-to-market).

- [x] **GBP→GB domestic payment rails — DONE (PR #343).** `bacs`,
      `faster_payments`, `chaps` added to the `PaymentMethod` enum, classified
      on both `payment_methods.py` axes (IRS-reportable + `DOMESTIC`), with fee
      anchors as `Decimal`. `pick_corridor` gets a GBP/GB branch:
      `not requires_fx and target_currency == "GBP" and country in (None, "GB")`
      → Faster Payments (no SWIFT, no FX lock, no IBAN — UK domestic uses sort
      code + account number even though `is_sepa_country("GB")` is true).
      Cross-currency into GBP still routes `international_wire`. No migration
      (`Payment.method` is a `String`); only the `mock` adapter gained the
      rails. Guards: `tests/test_payment_corridor_uk_domestic.py`, extended
      `test_payment_methods.py`.
      **Still open (separate finding, above):** the per-line VAT tax model —
      that's the architecture item, unrelated to the payment rail.

**Investigated, deliberately not changed (recorded so it isn't re-litigated):**

- `approve_payment_run` stays on `require_roles(ROLE_CFO)` rather than
  `require_permission(PERM_PAYMENT_RUN_APPROVE)`. Migrating it was tried and
  reverted during #330 — it let non-CFO admin/ap_manager bypass the
  CFO-approval-threshold control (a real regression caught by CI). The inline
  comment in `backend/app/api/payments.py` is the durable record.

### Persona-panel acknowledged gaps — ⚠️ PRODUCT REVIEW NEEDED (issue #328)

Eight capabilities the personas confirmed absent and classified as *product-fit
gaps* (the app never claimed them), not defects. **None is a bug — each is a
deliberate scope decision waiting to be made.** For each: **keep** it (→ add to
`docs/roadmap.md`, size it) or **drop** it (→ record as a documented non-goal in
`docs/competitive-analysis.md` / the relevant doc so it isn't re-filed every
persona round). The `[ ]` is checked when the keep/drop call is recorded, not
when the feature ships.

A suggested lean is given per gap — **`lean: keep`** / **`lean: drop`** /
**`lean: ?`** (genuine toss-up) — to make the review a yes/no rather than an
open discussion. Owner: a product/founder call; nothing here is Claude's to
decide.

- [ ] **No US sales/use-tax self-assessment** — no self-assessed use tax on
      out-of-state purchases, no nexus tracking, no resale/exemption
      certificates. `Invoice.tax_rate` only records what the vendor charged. US
      AP table stakes above a certain company size. **`lean: keep`** (real US
      mid-market requirement; large, own epic).
- [ ] **Positive Pay has no frontend** — `/api/positive-pay` is API-only (already
      noted as known state in the root `CLAUDE.md` architecture table).
      **`lean: keep`** (backend is done; a thin UI is a bounded slice, not an
      epic — cheapest of the eight to close).
- [ ] **`bank_details` has one generic `routing_number`** — no way to record a
      separate wire vs ACH routing number, common at larger US banks.
      **`lean: keep`** (small schema/UI change; blocks real payments at larger
      banks).
- [ ] **1099-MISC per-box allocation is not split** — the whole total goes to the
      requested box (documented simplification in `tax_1099_forms.py`).
      **`lean: keep`** (correctness for a shipped feature; medium).
- [ ] **No consolidated org-wide budget-vs-actual rollup on the CFO dashboard** —
      only the standalone `/budgets` page and per-budget `GET /budgets/{id}/spend`.
      **`lean: ?`** (nice-to-have; depends on whether budgets is a headline
      feature or a checkbox).
- [ ] **No saved views / per-list default view, and no keyboard shortcuts or
      command palette** anywhere in the app. **`lean: ?`** (power-user polish;
      high effort, diffuse payoff — defer unless a design partner asks).
- [ ] **No supplier-portal dashboard/home** — `/portal` redirects straight to
      `/portal/invoices`; a vendor gets no at-a-glance "N need nothing from you,
      M await your action". **`lean: keep`** (small; pairs naturally with the
      portal work already in PR #343).
- [ ] **Leading-zero / numeric invoice-number normalization** (`INV-001` vs
      `INV-1`) is not handled by the rule-based duplicate check; the
      semantic-similarity path is the intended backstop only when RAG is enabled.
      **`lean: keep`** (small, real duplicate-detection hole; RAG-off is the
      common config).

_Two further gaps were considered and explicitly declined (recorded so they
aren't re-raised): IR35 / contractor status (payroll/HR tooling, not AP), and a
dedicated `country_code`/`is_foreign` column on `Vendor` (superseded by the
filed W-8 finding, fixed in #330)._

### Surfaced while clearing the open-PR backlog (2026-09-02)

- [ ] **`isomorphic-dompurify` sits on a deprecated 3.x line whose declared
      Node floor excludes the Node 20 CI runs.** Every 3.2x release carries the
      upstream notice "Raised the minimum Node.js version (breaking) without a
      major bump. Use 4.x for the same code with correct semver, or pin 3.19.0
      for Node < 22.22.2", and declares
      `engines: ^22.22.2 || ^24.15.0 || >=26.0.0`. All four `setup-node` steps
      in `.github/workflows/ci.yml` pin `node-version: 20`. Nothing breaks today
      — pnpm does not enforce `engines` without `engine-strict`, and this is
      pre-existing (3.22.0 already declared the same range, so the #344 bump to
      ^3.23.0 changed nothing) — but the dependency is unmaintained-by-policy
      and the runtime it claims to need is not the runtime we build on.
      **Durable fix:** move the declared range to `^4.1.0` (the maintainer's own
      "same code, correct semver") *and* decide the supported Node floor in the
      same change, raising the CI pin off Node 20 to a version the dependency
      actually declares. Deliberately not done as part of the dependency-bump
      pass: the Node floor is a project decision with a blast radius past this
      one package, not a mechanical bump.
      **Trigger:** the next time CI's Node version is revisited, or the first
      time a transitive advisory lands on the 3.x line.

- [ ] **Dependabot's `pip` group does not group, so backend bumps arrive one PR
      per dependency.** `.github/dependabot.yml` declares
      `backend-minor-patch` with `update-types: [minor, patch]`, the same shape
      as the `npm` group's `frontend-minor-patch`. The npm group works — #344
      arrived on `dependabot/npm_and_yarn/frontend/frontend-minor-patch-…` with
      two dependencies in it. The pip group does not: #334, #335, #337, #339,
      #346 and #347 each arrived on their own
      `dependabot/pip/backend/<dep>-gte-…` branch. Because these locks have to
      be regenerated by hand (§ (b), no lockfile-sync workflow), the multiplier
      is the whole cost — six red PRs and six recompiles instead of one.
      **Candidate fix (unverified):** add `patterns: ["*"]` alongside
      `update-types` on the pip group, matching the two groups in this file that
      demonstrably do group (`actions`, `fake-erp`), both of which specify
      `patterns`. Not applied blind: a Dependabot config change cannot be
      verified without waiting for its next scheduled run, and an unverifiable
      guess committed as a fix is worse than a recorded observation.
      **Trigger:** next Monday's Dependabot run — apply the candidate and see
      whether the following week's pip bumps arrive as one PR.

- [ ] **Confirm the `packageManager` pin stopped Dependabot dropping the pnpm
      overrides.** Two npm PRs in one day (#344, #351) arrived with the whole
      `overrides:` block deleted from `frontend/pnpm-lock.yaml` while
      `package.json` still declared `pnpm.overrides` for `cookie@<0.7.0` and
      `undici@<7.28.0`, red on every job that installs. Root cause was that
      nothing declared which pnpm writes that lockfile, so four wrote it (CI on
      9, `audit.yml` on 10, contributors on whatever, Dependabot on its own
      default). Both package.json files now pin `pnpm@10.12.4` and every
      `pnpm/action-setup` site reads it instead of passing `version:`.
      The divergence is fixed and verified. Whether it also fixes Dependabot is
      a **hypothesis** — it cannot be tested without waiting for the next npm
      PR.
      **If it recurs:** regenerate by hand (`pnpm install --lockfile-only`, then
      confirm the block survived), exactly as § (b) describes for the pip locks.
      Do NOT reach for `--no-frozen-lockfile`: those are conditional floor
      guards against a future transitive downgrade, inert today
      (`cookie@0.7.2`, `undici@8.10.0` both already clear them), which is
      precisely what makes losing one easy to miss.
      **Trigger:** the next Dependabot npm PR. Recipe in
      [frontend/CLAUDE.md](../frontend/CLAUDE.md) § The lockfile.

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

- [ ] **OPTIONAL — no automated lockfile sync, so every Dependabot pip PR
      arrives red.** There is no lockfile-sync workflow any more:
      `.github/workflows/dependabot-lockfile.yml` regenerated the frontend pnpm
      lockfile and the backend `requirements{,-dev}.lock` and pushed the result
      onto the Dependabot branch, but its `push` job gated on a
      `DEPENDABOT_LOCKFILE_PAT` Dependabot secret that no repo in the estate has
      ever set (`gh api repos/:owner/:repo/dependabot/secrets` →
      `total_count: 0`), and the gate treated the missing PAT as skip-and-succeed
      so the inert workflow reported green. #325 removed it rather than leave a
      workflow that burned runner minutes and lied about it.
      Consequence, unchanged either way: a Dependabot manifest bump arrives red,
      because the stale lock fails
      `backend/tests/test_dependency_lock_sync.py`, and the locks get
      regenerated by hand instead (commands in
      [backend/CLAUDE.md](../backend/CLAUDE.md) § Dependency lock — roughly two
      minutes, and cheaper still in a batch: six pip PRs (#334, #335, #337,
      #339, #346, #347) were cleared in one recompile once they had piled up).

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

      **Durable fix (when wanted):** restore the workflow from the templates
      repo — `project-flakey` keeps the fixed version, which fails loudly
      instead of skipping when the credential is absent — and give it a
      fine-grained PAT scoped to this repo with `Contents: Write`, stored in the
      **Dependabot** secret store, NOT the Actions store, since
      Dependabot-authored PRs cannot read Actions secrets:
      `gh secret set DEPENDABOT_LOCKFILE_PAT --app dependabot`. A GitHub App
      token via `actions/create-github-app-token` is the sturdier variant — no
      expiry, not bound to one person's account. The credential is what makes
      the push retrigger CI at all: a `GITHUB_TOKEN` push does not start a new
      workflow run, so the PR would stay red on its original failed check even
      after the lock was fixed.
      **Trigger:** when regenerating locks by hand becomes a recurring
      irritation — not on any fixed schedule.
