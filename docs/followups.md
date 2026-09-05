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

**Last reconciled:** 2026-09-05 (round 19) — a seven-agent sweep: six on this
file, one an adversarial review of round 18's own 146-file diff.
**Five entries closed, two opened**, 27 → 24.

The review was the point of the round. Ten agents had landed round 18 in one
shared working tree with no review pass, so it was aimed at the failure modes
that process actually produces — seam defects, convention drift, and edits
clobbered by a concurrent write. It found **one real seam defect**: the
bank-reconciliation e2e spec still mocked `uncleared_total` as a scalar after a
later commit in the same round renamed it to a per-currency array, and the page
spreads that value, so the stub made the real page throw. It also **cleared**
what mattered most — the BEC dual-control gate intact across all four
bank-detail write paths, both extraction guards honoured in both dispatch modes,
the 1099 reconciliation guarantee holding by construction — and diffed all 3,828
keys across the six locale files, written concurrently by four agents, finding
no lost translations.

Two findings this round were things a follow-up had named but nobody had
measured:

- **An unused dependency was setting the runtime the whole project builds on.**
  The entry asked to bump `isomorphic-dompurify` to 4.x and raise the Node floor
  to match. It has **zero call sites** — the XSS defence here is that nothing
  uses `{@html}` at all — so it was removed instead, and the floor now belongs to
  `jsdom` as vitest's own peer. The floor moved at **nine** `setup-node` sites,
  not the four the entry named, plus `deploy/deploy.sh`, which builds the
  *production* frontend and was still on EOL Node 20
  ([decisions.md](decisions.md) §85).
- **The budget rollup's 3-queries-per-budget was worth 600 queries / 297 ms at
  200 budgets.** Now 6 / 8.6 ms — and the interesting part is what it cost to get
  there honestly: correlating the predicates lost the planner its index and
  regressed the *single-budget* path (which `GET /budgets/check` sits on before
  every requisition submit) from 0.6 ms to 10.4 ms, so the fix carries redundant
  set-level narrowing predicates to restore it. The anti-drift guard is
  mutation-tested ([decisions.md](decisions.md) §82).

Two process notes worth keeping:

| What happened | Why it matters |
|---|---|
| Both metric-definition halves are now closed, and both **move a reported number**. §73 stopped imported `done` rows inflating the touchless rate; §81 stops imported `rejected` rows deflating it. The second needed a *provenance* marker, because status cannot identify an import — `done`/`paid`/`rejected` are each reachable both ways | A metric fix that changes a figure leadership reads has to say so. `backend/docs/analytics.md` records the direction and the cause, so a dashboard delta is not misread as an automation regression |
| An agent's `flutter analyze` generated CocoaPods scaffolding into the tree, and it nearly rode along in a commit | Tool-generated artifacts are not work. Check `git status` against what you actually changed before a path-scoped commit, not after |

New this round: [decisions.md](decisions.md) §81–§85.

**Previously reconciled:** 2026-09-05 (round 18) — a ten-agent parallel sweep.
**Eleven entries closed, four opened**, 34 → 27. Two whole categories are gone:
the frontend money-typing ratchet stands at **zero across every module in
`src/lib/types/`**, and `/bank-reconciliation` — the last shipped backend with
no UI at all — now has one.

The round's sharpest finds were not in any entry, and three of them were
*produced by* closing one:

- **The touchless / automation rate was inflated for exactly the tenants with
  the least automation to show.** `done` counted as "cleared review" on status
  alone, but `new → done` skips approval outright *and* the Day-0 CSV importer
  plants historical rows straight at `done` — its default — with the workflow
  engine never running. A tenant migrating ten thousand historical invoices
  reported near-100% automation on day one ([decisions.md](decisions.md) §73).
- **Approval-gate thresholds went to the wire as floats.** `require_cfo_above`,
  `auto_approve_below`, `max_invoice_amount` and the chain's `min`/`max_amount`
  were `parseFloat`ed on every keystroke, and unreadable text was sent as
  `null` — **silently removing the CFO gate**. Found by retyping the fields, not
  by looking for it; the same defect was then found in six more forms.
- **A "your routing number has a typo" 4xx answered with the account number.**
  The checksum ran in a Pydantic `field_validator`, and FastAPI renders that as
  a 422 whose body echoes the rejected `input` — the whole `bank_details` dict.
  The supplier portal had the identical validator and the identical leak
  ([decisions.md](decisions.md) §74).
- **Re-extracting a resubmitted invoice needed a second guard nobody had
  scoped.** The follow-up named `skip_vendor_match`; re-extraction also re-enters
  `decide_auto_approve`, so a tenant with unattended approval would let a
  supplier launder a human-rejected invoice past the reviewer who rejected it
  ([decisions.md](decisions.md) §75).
- **Two more silent-partial-figure defects**, both the same shape as the
  disclosure work that surfaced them: budget spend legs *dropped* every
  foreign-currency row while reporting `committed`/`actual` as complete
  ([decisions.md](decisions.md) §79), and bank-rec's outstanding buckets summed
  `Payment.amount` across currencies under one symbol.
- **`/goods-receipts` badged every status green**, so a cancelled / voided /
  reversed receipt read as a successful delivery — the same row backend PO
  matching deliberately excludes from the 3-way quantity leg.

Two process notes worth keeping:

| What happened | Why it matters |
|---|---|
| An agent's broad sweep reported 8 failures in `test_vendor_portal_isolation.py`, which asserts against `inspect.getsource(...)`. The file had been rewritten **during** the 8-minute run. Stale `linecache` data, not a defect | A source-scanning test is not safe to read as a signal while the tree is moving. Re-run against a stable tree before believing a failure that only appears in a long sweep |
| Closing "`positivePay.ts` parses money with `parseFloat`" last round set the pattern this round applied seven more times: a preview that **repairs** unreadable input is how a wrong figure reaches a field the user then trusts | The fix is a helper that returns `null` and a caller that renders a dash — `scaleMoney` ([decisions.md](decisions.md) §80) is that helper for the multiply case `sumMoney` never covered |

One entry was closed as **stale rather than done**: "Positive Pay has no
frontend" — the route, its API module, a role-gated nav entry and e2e coverage
all already existed. Its parenthetical blamed the root `CLAUDE.md`, which in
fact says that of `/bank-reconciliation`, and that one was true until this round.

New this round: [decisions.md](decisions.md) §73–§80.

**Previously reconciled:** 2026-09-04 (round 17) — a seven-agent sweep over what
round 16 left. **Fifteen entries closed, none opened**, 41 → 34. The whole
round-15 narrative section is gone: all eight of its findings were fixed, so
that section no longer exists.

The round's sharpest find was not in any entry. Closing one deferral
(`auto_approve_below` mixing currencies) exposed the layer above it, and
measuring it against the pre-fix code showed a **GBP 9,000 invoice — USD 11,403
at the rate locked on its own row — approved by an `ap_manager` with no CFO
signature**, under a USD 10,000 `require_cfo_above`, and routed to the manager
tier of a chain whose senior level starts at 10,000. `require_cfo_above` decides
whether a CFO signs at all. Five threshold comparisons now share one *value*
(`approval_chain.GateAmount`, carrying the figure and whether it could be
established) rather than a convention — because a convention is what the sixth
comparison forgets ([decisions.md](decisions.md) §71).

Four more defects surfaced that no entry had named: a `<select>` fed by a
paginated list showed only its first 20 options, so on the demo tenant
**creating a credit memo against most suppliers was impossible**;
`positivePay.ts` parsed a malformed cheque amount to `null`, which the
classifier reads as `matched_ok` — the altered-cheque control passing on
unreadable input; the supplier-portal login carried the **same** existence
oracle as the employee one, and worse (it opens a second control-plane session
a known address pays for); and a legacy vendor-user row with no
`organization_id` returned from *inside* the audit helper, a third timing
signature that was neither branch.

Three process notes worth keeping:

| What happened | Why it matters |
|---|---|
| Three new tests **passed in isolation and failed in the full suite**. The realdb harness truncates tenant data between tests but not control-plane users, and `test_admin_user_management` renames the shared `ap_clerk` without restoring it — so the assertions failed on test ORDER | Identity is the **id**; `full_name` is a field an admin can PATCH. Asserting on a mutable display name in a suite with shared control-plane rows is brittle by construction. A 28-minute reproduction was narrowed to 33 seconds before anything was touched |
| A brief said to queue the login audit "the way `post_commit` queues notification legs". Reading `post_commit` first showed that would have been **silently wrong** — it fires from `after_commit`, and a failed login rolls back, so `after_rollback` drains and discards the queue by design | The row would never have been written at all. Read the mechanism before reusing it; a plausible analogy is not a verified one |
| A brief asked for contrast ratios "in both themes". There is only one palette — `app.css` sets `color-scheme: dark`, with no `prefers-color-scheme` block and no `[data-theme]` anywhere | The agent said so rather than inventing a second set of numbers |

New this round: [decisions.md](decisions.md) §70–§72. `docs/known-issues.md`
gained one entry — two `queue-blocked` e2e cases that survive the page-one fix,
recorded with what was established, what was **not**, and the diagnostic to run
first.

**Previously reconciled:** 2026-09-04 (round 16) — a ten-agent parallel sweep whose
brief was this file itself: take the open entries that are code-fixable, fix them
at the root, and close them. **Twenty entries closed, four opened**, 57 → 41.
The count survives the merge with #359 by coincidence rather than accident: that
PR closed the rebate-denomination entry this branch still carried open
([decisions.md](decisions.md) §62) and opened one of its own (the `number`-typed
frontend money fields, below), so the file lands at 41 either way.

Four things are worth carrying forward, because they are about how the entries
themselves were written rather than about the code:

| What happened | Why it matters here |
|---|---|
| **Three entries were stale.** `card_adapters` and `positive_pay_adapters` had already been converted in round 15; `total_paid`'s cross-currency SUM had already been fixed by `d92e0bef`. Each agent verified before rewriting, and folded the existing fix into its own test coverage instead | An entry is a claim about the code at the time it was written. Re-read the source before acting on one — and add a regression test for the already-fixed half, so a silent revert fails loudly rather than quietly re-opening the entry |
| **Two entries under-described their own defect.** The money-filter entry named the `float` parameter; the *second* rounding was SQL-side, where asyncpg renders `$1::NUMERIC(15,2)` and Postgres rounds an over-precise bound back onto the boundary row ([decisions.md](decisions.md) §65). The PO entry named a cross-entity *read*; one route away the same unscoped `po_number` match was a cross-entity **write** that re-priced another subsidiary's PO | Retyping alone left the behavioural tests still failing, which is how the second rounding was found. Treat an entry's diagnosis as the starting point, not the specification |
| **One entry's stated reason was wrong.** The `recurring_invoices` entry said `uq_invoice_recurring_period` "does not cover" the failure mode without saying why. It doesn't because the unguarded bug writes a *first* invoice for a period that is not due yet, on a key the index accepts ([decisions.md](decisions.md) §66) | The reason is the load-bearing part. An entry that asserts a conclusion without it invites the next reader to re-derive it — or to accept it and get the fix wrong |
| **One brief was wrong and the agent said so.** Adding `failed` to both legs of `touchless_rate`, as the entry implied, would have inflated the metric off invoices nobody ever approved; `failed` is reachable from `pending` *and* `sending_to_erp`, so it is split on `Invoice.approval_date` | The entries are not specifications. Where one conflicts with the code, the code wins and the entry gets corrected |

Nine defects were found that no entry had named, each fixed with the slice that
surfaced it: a rolled-back payment transition still queuing the ERP sync that
marks an invoice **paid**; `contract_renewal`'s two passes able to roll back each
other's already-emailed alerts; a partial `ORDER BY` that lets two replicas
deadlock; a budget-currency 422 firing before the entity check and confirming a
hidden row's denomination; `POST /api/discounts/optimize` running **unconstrained**
on a misspelled key; a TIN-validation fallback that would have *un-verified* a
correctly verified vendor; the punch-out cart-return endpoint accepting a payload
shape the tenant's protocol never configured; `qms_sync` fabricating `completed`
inspections against real POs and advancing its cursor past the window it never
pulled; and three existing tests that were **pinning defective behaviour**
(`test_analytics.py`) or asserting a fallback this round removed.

Guard rail 6 was applied to the round's own output: every deferral an agent
raised mid-round was re-dispatched and closed rather than filed, except the four
below — which are forward work or a population question, not diagnosed defects.
The fail-open adapter-registry class is now **closed entirely**: 16 of 21
dispatchers fail closed, the remaining five carry a re-verified reason, and a
classification guard means a new registry cannot fail open silently
([decisions.md](decisions.md) §63).

New this round: [decisions.md](decisions.md) §63–§69. `docs/known-issues.md`
stays empty — nothing was diagnosed and left unfixed.

**Previously reconciled:** 2026-09-04 (second pass) — a five-agent coverage pass over
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

At the time of that pass this file carried **57** open checkbox entries (plus the
8 narrative round-15 findings); round 16 took it to **41**. The money-path pass
took the checkbox count 64 → 56; the coverage pass that followed it added one back (the `float` money
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
      **Progress (rounds 18–19):** round 18 took **nine files to zero** —
      `Landing`, `InvoiceModal`, `/credit-memos`, `/exceptions`,
      `/goods-receipts`, `/organization`, `/purchase-orders`,
      `/vendors/change-requests`, `/workflows`. Round 19 took the two tranches
      this entry named next: `types/payment.ts` + `/payments` + `RunDetailModal`
      **together** (the modal badges the same `PaymentStatus` union, so a local
      tone map would have manufactured the drift the shared-map convention
      prevents), then `/expenses` + `/requisitions` with the tone-map hoist into
      `types/{expense,requisition}.ts`. The deliberate keeps are grouped under
      their own divider, so what remains is legible: **12 rules in 5 files** —
      `/discounts` 4, `/tax` 3, `/vendors` 3, `/invoices` 1,
      `/vendor-statements` 1.
      Two defects surfaced on the way, both fixed: `/goods-receipts` badged
      **every** status green, so a cancelled / voided / reversed receipt read as
      a successful delivery (the same row `po_matching.CANCELLED_GR_STATUSES`
      excludes from the 3-way quantity leg); and `RunDetailModal` tinted a
      **draft** run amber while `/payments` rendered the same run flat neutral,
      one click apart. A third was structural: the audit's own self-check pointed
      at `/expenses`, which the tranche made clean — it would have started
      passing vacuously, and now names a file with a live baseline.
      **Durable fix:** convert the rest in attributable tranches, checking
      collapsed distinctions as you go, and editing the baseline down in the same
      commit.
      **Trigger:** the next slice touching any file the baseline names.

### Surfaced by the round-19 parallel sweep (2026-09-05)

- [ ] **(c) `invoices.cost_center` and `invoices.gl_account` carry no index.**
      `department` and `project` do. A budget on either unindexed dimension
      seq-scans the invoice table when no entity narrows the set. The round-19
      rollup rewrite left this **no worse than before** — its set-level narrowing
      predicates restore the same index scan the old query used wherever an index
      exists — so this is a pre-existing ceiling, not a regression.
      **Durable fix:** an index on each, in a migration that fans out to every
      tenant DB. Measure first: on the benchmark tenant the invoice leg is ~1 ms
      at 40k invoices, so this is not yet costing anything.
      **Trigger:** a tenant whose budgets are cost-center- or GL-dimensioned
      showing the rollup or `GET /budgets/check` in latency traces.

- [ ] **(c) The touchless metric has no backfill for pre-marker imports.**
      Rows the CSV importer created before `Invoice.meta["imported"]` shipped
      carry no marker and are therefore counted as native, so a tenant that
      migrated history before 2026-09-05 still has those rows in the population
      ([decisions.md](decisions.md) §81).
      **Why there is no backfill:** stamping a historical row on an inference is
      exactly the guessing the marker replaces. Status cannot identify them
      (`done` / `paid` / `rejected` are each reachable both ways) and neither can
      creation time on its own.
      **Durable fix, if wanted:** an operator-run, opt-in, date-bounded stamping
      tool where the **operator** asserts the cutover date — the assertion has to
      come from someone who knows when the migration ran, not from the data.
      **Trigger:** a tenant asking why its automation rate looks wrong for a
      period predating the marker.

### Surfaced by the round-18 parallel sweep (2026-09-05)

Four items the round-18 agents traced to a file and line but correctly did not
fold into their own slice. None is a defect that can bite today.

- [ ] **(c) No live payment adapter consumes the wire ABA yet.**
      `resolve_routing_number` picks the right routing number per rail, but every
      shipped adapter identifies the payee by a processor **counterparty token**
      and transmits no raw bank coordinates — so today the resolver's only live
      consumers are the `mock` adapter and Positive Pay's ACH file (which reads
      the ACH number and is correct unchanged). The wire number is stored,
      staged under dual control, and surfaced; it is not yet transmitted.
      **Why it is not a defect:** the field had to exist before a counterparty
      provisioning path could send it, and the resolver is what makes the rail
      distinction unambiguous when one arrives.
      **Durable fix:** counterparty provisioning at the processor, which this
      codebase does not model at all.
      **Trigger:** wiring a payment adapter that hands a bank raw coordinates.
      See [decisions.md](decisions.md) §74.

### Surfaced by the round-16 follow-up sweep (2026-09-04)

Four items the round-16 agents traced to a file and line but correctly did not
fold into their own slice. None is a defect that can bite today; each is either
forward work or a population question with its own consequences.

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
- [ ] **No saved views / per-list default view, and no keyboard shortcuts or
      command palette** anywhere in the app. **`lean: ?`** (power-user polish;
      high effort, diffuse payoff — defer unless a design partner asks).
### Surfaced while clearing the open-PR backlog (2026-09-02)

- [ ] **(b) The Dependabot pip-grouping fix is applied but UNVERIFIED.**
      `patterns: ["*"]` was added alongside `update-types` on the
      `backend-minor-patch` group on 2026-09-05, matching the two groups in that
      file that demonstrably do group (`actions`, `fake-erp`). A Dependabot
      config change cannot be verified without waiting for its next scheduled
      run, so this is a candidate, not a fix.
      **Note the hypothesis is already partly contradicted:** the `npm` group has
      the identical no-`patterns` shape and groups anyway, so `patterns` may not
      be the operative difference.
      **Confirmed if** next Monday's pip bumps arrive on one
      `dependabot/pip/backend/backend-minor-patch-…` branch; **refuted if** they
      again arrive as separate `dependabot/pip/backend/<dep>-gte-…` branches.
      **If confirmed:** apply the same one-liner to `terraform-minor-patch`,
      which carries the same untested shape and was deliberately left alone.
      **Trigger:** next Monday's Dependabot run.

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
