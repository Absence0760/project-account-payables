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

**Last reconciled:** 2026-09-06 (round 23) — ten agents: four closing this
file's actionable remainder, six hunting. **31 → 56.**

The growth is the finding, not a failure of the round. The hunters were pointed
at areas the last two rounds had already swept, and still returned 34 verified
defects — because the previous sweeps looked for *unwired code*, and these
looked for *two things that should agree and were never compared*. That framing
found a segregation-of-duties control that one person can walk around, four
money-path gaps, five drift guards that do not bite (one with a demonstrated
bypass), 14 documentation statements contradicting the code, and a landing-page
query 29× slower than its own neighbours.

**The two segregation-of-duties items are held for security review** and are
marked as such below. They are SOC 2 CC6.3 controls; changing who may approve
what is a control-design decision, not a bug fix.

The tenant-isolation and authz audit came back **clean**, which is the one
result here worth stating positively given how fast rounds 20-22 shipped.

Three of the closures found something the entry had not predicted, which is the
argument for working this file rather than around it: the two `approval_levels`
spellings were a latent `AttributeError` on the approval path rather than style
debt ([decisions.md](decisions.md) §93); a routine branding save would have
silently wiped an IdP-registered SSO callback and taken every SSO login with it
(§92); and one endpoint had to perform a write to establish a read-only fact
(§94).

The count grew on purpose. The two sweeps found substantially more real,
verified work than a ten-agent budget could land; everything with a **live
defect** behind it was fixed in the same round (the ERP retry divergence, the
half-validated UK bank pair, the unpinned SSO authorize helper, the SCIM digest
owner, and the e-invoice generator's BT-106/BT-109 collision), and the verified
remainder is recorded below rather than dropped. A follow-up file that shrinks
while its sweeps keep finding things is measuring the wrong quantity.

Round 21 closed the white-label vanity-domain trio the round-20 runbook
surfaced (SPA host resolution, per-tenant outbound links, per-tenant passkeys)
and narrowed the BIS Billing 3.0 conformance entry to its official-Schematron
half; round 22 closed the last piece, SSO on a vanity host. **Roadmap Priority
13 is complete and archived.**

What did **not** move, and why, so it is not re-surveyed next round: the (a) and
(b) sections are unchanged and remain blocked on things this repo cannot
produce — an external credential or account, a scheduled Dependabot run, a live
Teams tenant to observe, or a product/founder decision. The FatturaPA XSD and
the official EN 16931 / PEPPOL Schematron are still deliberately unvendored:
pulling third-party schema files into a **public** repo is a licensing call, and
both triggers are unmet. The eight persona-panel product-fit gaps still await a
keep-or-drop call, which is not Claude's to make.

**Round 19's own narrative has been pruned.** It recorded eight defects that
round found and fixed — an unused dependency setting the project's Node floor,
the budget rollup's 600 queries, the inflated touchless rate, float approval
thresholds, a 4xx that echoed an account number, and three more. All are
shipped; the reasoning lives in [decisions.md](decisions.md) and the code in git
history, which is where this file's own rule says it belongs. Nothing from it
remains open.

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

- [ ] **BIS Billing 3.0 conformance is our own re-implementation, not the
      official Schematron.** NARROWED (round 21). The calculation rules
      (BR-CO-*) and code-list membership this entry named are now implemented
      (`services/e_invoice/{en16931_rules,codelists}.py`) and CI-tested, each
      rule with a satisfying and a violating document. Adding them immediately
      earned its keep: the generator mapped `Invoice.subtotal` into **both**
      BT-106 and BT-109, so every invoice carrying a discount or a shipping
      charge went out contradicting itself on BR-CO-13/15/17 with our
      conformance claim on it — fixed at the source in `mapper.py`
      ([decisions.md](decisions.md) §90).
      **What is still open:** (a) rules whose inputs the normalized model has no
      slot for — allowance/charge detail, invoicing periods, VAT point dates
      (tabulated in `en16931_rules`' docstring); (b) membership for UN/ECE Rec 20
      units, UNCL4461 payment means and the CEF EAS scheme list, which get a
      *shape* check only because a partial list would 422 a genuinely conforming
      send; (c) the fact that a pass still means "nothing we can compute
      objects", not conformance. The asymmetry that makes the conditional
      declaration sound is unchanged: a **failure** provably does not conform.
      **Durable fix:** vendor the official EN 16931 + PEPPOL Schematron into
      `backend/tests/fixtures/` and assert generated documents validate in CI.
      Not done this round on purpose — this is a public repo and vendoring
      externally-licensed validation assets is a call to make deliberately.
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

### Tinted badges — the primitive owns the recipe; what's left is deliberate keeps

The contrast half of this entry is long closed (the 29 badges below 4.5:1, fixed
via tint-paired text tokens — [decisions.md](decisions.md) §30). Round 12 closed
the **ownership** half: `frontend/src/lib/components/ui/Badge.svelte` is now the
single owner of the tinted-badge recipe. A caller names a *tone* and cannot spell
it wrong; `variant` passes the caller's semantic class through as a **selector
hook only** (the e2e suite reads `.badge.approved`), never as colour. Rationale,
including why sizing is fixed rather than a prop and why `neutral` / `erp` stay
non-tinted: [decisions.md](decisions.md) §47.

**The tranches are done — CLOSED (round 20).** `/discounts` (4),
`/tax` (3), `/vendors` (3) and `/vendor-statements` (1) went to zero, each its
own attributable tranche with the baseline edited down in the same commit. The
two shared-status pages hoisted a `STATUS_TONES` map beside the existing
`STATUS_LABELS` (`types/discounts.ts`, `types/vendor.ts`) so a list page and its
detail modal cannot disagree — the convention rounds 18–19 established.

`/invoices`' `.priors-badge` was the one conversion **refused**, and it moved
above the divider as a deliberate keep. It is not a status: it is an extraction
provenance annotation (`RAG·2·cache·3`, `cursor: help`) rendered *inside the
vendor cell beside the vendor name*, in a fixed-width ellipsised column. The
primitive's metrics would crowd out the name it annotates — the `UsersPanel`
`.you-badge` case verbatim, already an accepted keep for that reason — and it
already takes the `--accent-tint` / `--accent-on-tint` pair, so its colour
cannot drift.

Two things follow, and both are now enforced rather than remembered. The
`--- Still to convert ---` divider is **gone**: every remaining baseline entry
is a keep carrying its own reason, so a new non-zero entry is a keep that must
argue for itself, not a tranche waiting to land. And the audit's own self-check
(`it('detects the recipe it is meant to detect')`) pointed at `/vendors`, which
this round took to zero — it would have begun passing vacuously, so it now
names `ScreeningBadge`, a *permanent* keep that cannot be invalidated by the
next conversion.

### Surfaced by the round-20 parallel sweep — CLOSED (rounds 21-22)

All three entries here said a white-label vanity domain did not really work.
Two are now closed and the third is narrowed to one remaining piece.

**The SPA resolves a vanity host — CLOSED.** The diagnosis was right and the
proposed *cheap half* (validate the first label against the tenant slug, so the
panel refuses the broken shape) was **rejected in favour of the real fix**: it
would have made the panel honest while permanently narrowing the product to
`<slug>.<customer-domain>`, which is not what a customer buys a vanity domain
for. Instead the SPA classifies the hostname against an operator-declared
`PUBLIC_PLATFORM_DOMAINS` (`frontend/src/lib/hostRouting.ts`) and sends **no**
`X-Tenant-Slug` on anything that is not a platform host, which is what finally
reaches the backend `Host` fallback that has existed all along. The API origin
resolves at runtime and collapses to same-origin `/api` on a vanity host,
because a request to the build-time API origin carries the *platform's* `Host`
and defeats the lookup either way. Both layouts now gate on `hasTenantContext()`
instead of the slug — without that they rendered the marketing landing page to a
customer on their own domain, so the rest would have been invisible.
Unset config replays the old rule byte-for-byte, so no existing build changes on
upgrade ([decisions.md](decisions.md) §86).

**Per-tenant outbound links — CLOSED.** `settings.brand.tenant_url_template`
overrides the global, resolved by one `app/utils/tenant_urls.py` that all
**ten** call sites read — not the six this entry claimed; `services/supplier_chat`
and `services/card_issuance` both did their own substitution and did not look
like template call sites from the outside. An unresolvable base is now a real
answer: callers omit the link rather than fabricate a `localhost` URL into a
customer's inbox ([decisions.md](decisions.md) §91).

**Per-tenant passkeys — CLOSED.** RP ID and origins resolve from the tenant's
own registered custom domains, from the org that owns *the account*, never from
a `Host`-driven lookup — so a forged host, an unknown host and another tenant's
vanity domain all fail closed to the platform RP. The migration story this entry
said "needs designing, not just a config field" was designed and shipped:
`webauthn_credentials.rp_id` (migration 0091), `usable_here` on the list
endpoint, and a named cross-host message so a credential registered elsewhere
reports itself instead of failing opaquely ([decisions.md](decisions.md) §87).

**SSO on a vanity host — CLOSED (round 22).** The last piece. `slug` is now
optional on the four SSO/SAML entry points, falling back to the existing
`resolve_tenant_slug_by_custom_domain`; an unresolvable `Host` reuses the
**existing** 404 verbatim, so no enumeration surface was added (a test asserts
the two exceptions are equal, not merely both 404s). The callback base URL is a
**separate** opt-in field from `tenant_url_template`, because it is registered
at the customer's IdP and folding the two would mean fixing invite links
silently breaks SSO ([decisions.md](decisions.md) §92); the runbook carries the
ordered re-registration. Closing it surfaced a defect the entry had not
predicted: a routine branding save would have silently wiped that callback,
since it IS a `BrandConfig` field and `model_dump()` emits `""` for an omitted
one. `BUILD_TIME_API_URL_BASELINE` is now empty — the ratchet shrank to zero as
designed, and roadmap Priority 13 moved to the archive.

- [ ] **(b) True end-to-end e2e for the vanity host needs two env lines.** The
      unit layer covers the classification rules exhaustively and the e2e spec
      locks the *platform*-host half (the catastrophic regression path — a
      platform subdomain must keep sending its slug and keep calling the
      build-time origin). The vanity half is unit-only because
      `PUBLIC_PLATFORM_DOMAINS` reaches neither run mode: locally the suite
      boots `pnpm dev` (which loads `.env.development`), and CI serves a
      production-mode `vite build` that does not. Unset means *no host is a
      vanity host*, so the same navigation would assert opposite things in the
      two environments — which is why the spec does not fake it.
      **Durable fix:** add `PUBLIC_PLATFORM_DOMAINS` beside `PUBLIC_API_URL` in
      `playwright.config.ts` `webServer.env` **and** in the CI build step, then
      add a second hostname that serves both the SPA and `/api` (neither `vite
      dev` nor `vite preview` proxies `/api`, so a same-origin call 404s at the
      static server today).
      **Trigger:** the same first vanity-domain onboarding.

### Surfaced by the round-19 parallel sweep (2026-09-05)

**Indexed — CLOSED (round 20).** Migration `0090_invoice_budget_dim_idx`,
gated on the `invoices` table existing (the shape 0044 and 0088 use) so it
no-ops on the control plane and fans out via `migrate_all_tenants.py`; the model
declares `index=True` so `create_all`-provisioned tenants match, and the names
follow SQLAlchemy's default so the two provisioning paths cannot diverge.

Measured before landing, as this entry demanded. Median of 7 warm runs against
the SQL `budget_service._actual_invoice_legs` actually emits, on a scratch
tenant with **independently randomised** dimension and status — a first
generator keyed both off `i % N`, correlating them into a misleading zero-row
case, and was discarded. `department` is the control and must not move:

| Invoices | `cost_center` | `gl_account` | `department` (control) |
|---|---|---|---|
| 40 000 | 7.7 ms → **1.8 ms** | 6.5 ms → **1.5 ms** | 2.7 ms → 2.7 ms |
| 200 000 | 15.9 ms → **7.4 ms** | 14.9 ms → **6.7 ms** | 9.6 ms → 9.6 ms |

Seq Scan → Bitmap Index Scan; buffers 1003 → 578 at 40k, which is the durable
number — seq-scan cost grows with the *table*, index-scan cost with the
*matching subset*.

**What it does not fix, recorded so nobody re-measures it hoping:** the
whole-tenant rollup over ~half the distinct cost centers is 10.3 ms → 9.1 ms —
inside noise, and slightly *more* buffers. At that selectivity a seq scan is the
right plan. This is a fix for the *selective* path — `GET /budgets/{id}/spend`
and `GET /budgets/check`, the latter running before every requisition submit.

The guard test walks every `BudgetDimension` through `_DIMENSION_MATCH_COLUMN`
to an indexed column, so a fifth dimension added on an unindexed one fails
rather than silently reintroducing the asymmetry.

**The operator can now assert the cutover — CLOSED (round 20).**
`backend/scripts/backfill_import_provenance.py` is exactly the tool this entry
specified and nothing more: `--cutover` is required, has no default, and is the
only thing that sets the boundary. Nothing is inferred from the data — that was
the whole point ([decisions.md](decisions.md) §81).

Safety properties, each with a test: dry run is the **default** (`--apply` is
the only mutating switch), one **named** tenant (there is no all-tenants mode),
the bound is strictly `created_at < cutover`, a **future** cutover is refused (a
migration that has not run produced no history, and that date would stamp live
invoices), and candidates come through `csv_import.native_invoice_clause()`
rather than a restated predicate, so an already-marked row is excluded in SQL
and a re-run can neither double-stamp nor overwrite a real `csv_import` marker.
A stamping run appends one PII-free `invoice.import_provenance_backfilled` audit
row; a dry run writes nothing. The marker records that it was *asserted* rather
than observed (`source=operator_backfill`, `asserted=true`), so a later reader
can tell a declared provenance from a recorded one.

**The smoke run changed the design, and it is worth knowing why.** A date bound
alone over-captures: on the dev tenant a cutover of today proposed 90 rows, 58
of them in statuses `csv_import` provably cannot land. Stamping those would have
*deleted genuinely native invoices from the metric* — the exact failure the
no-backfill rule existed to prevent, arriving through the front door. So the
tool restricts to importable statuses (imported from `csv_import`,
drift-guarded) and reports the rest as skipped. That is not the rejected
identify-by-status inference: it only ever refuses to mark, and a refusal leaves
the row reading exactly as it does today.

The caveat is in `backend/docs/analytics.md` in full: a wrong date mis-stamps in
either direction, neither direction is detectable from the data, and the tool
does not reverse it.

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

Two capabilities (of an original eight) the personas confirmed absent and classified as *product-fit
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

### Surfaced by the round-21 parallel sweeps — mostly CLOSED (round 22)

The round-21 sweeps found more verified work than that round's agent budget
could land, and it was recorded here rather than dropped. Round 22 spent ten
agents on it. **Closed:** the email-intake panel + token rotation, `/admin/entities`,
the `/api/adaptive` router's UI, the `/api/inspections` UI, the
approval-signature verification panel, `/health/sweeps`, the card-rebate
lifecycle, mobile CFO run approval, the `approval_chain` ownership + drift guard,
the PEPPOL transmission read path, and the e-invoice structured error contract.

Three of those turned up something the entry had not predicted, recorded in
[decisions.md](decisions.md): the two `approval_levels` spellings were not a
style difference but a latent `AttributeError` on the approval path (§93); a
routine branding save would have silently wiped an IdP-registered SSO callback
(§92); and `GET /organization/email-intake` had to perform a write to establish
a read-only fact (§94).

What remains from those sweeps:

- [ ] **(c) `workflow_step_types.is_canonical_step_type` is unused and
      untested.** Unchanged from round 21 — `workflow_builder` asks the inverse
      question via `BUILDER_STEP_TYPES` and handles aliases correctly.
      **Durable fix:** delete it, or give it a caller and coverage; an untested
      third spelling in the module that gates a financial control
      ([decisions.md](decisions.md) §32) is the wrong kind of spare part.
      **Trigger:** the next workflow-step-type change.

- [ ] **(c) `scheduled_reports.compute_next_run`'s docstring is false.** It
      claims to be "used when seeding a brand-new schedule", but the API
      deliberately seeds `next_run_at = body.next_run_at or now()` so an
      operator can watch the first run happen. A doc fix. **Trigger:** any edit
      to the scheduled-report runner.

- [ ] **(c) Smaller caller-less surfaces, still verified open.**
      `POST /api/payments/corridor-quotes`;
      `POST /api/exceptions/{id}/agent-resolve` (the agent dashboard reports on
      activity it cannot trigger); `POST /api/discounts/bulk-negotiate`; the two
      `/api/enrichment` read endpoints. Round 22 closed the audit-verification,
      sweep-health and card-rebate members of this group.
      **Durable fix:** each is a small addition to a page that already exists.
      **Trigger:** take them opportunistically when next in that page.

- [ ] **(c) `INTAKE_STATUS_LABELS` / `REQUISITION_STATUS_LABELS` are hardcoded
      English.** Unchanged. In `src/lib/types/*.ts` and not routed through i18n,
      so a German user sees a translated Reopen confirm next to an untranslated
      `DRAFT` badge. **Durable fix:** move both maps behind `MessageKey`s like
      the other status-label sets. **Trigger:** the next i18n or procurement
      slice.

- [ ] **(c) `POST /api/invoices/{id}/route-intercompany` still has no UI.**
      Inter-company mirror generation, documented and scored a competitive
      "Have". It was moot while a tenant could not create a second entity;
      `/admin/entities` (round 22) removes that excuse, so this is now a real
      gap rather than a blocked one. **Durable fix:** an action on the invoice
      detail modal, gated admin/ap_manager and on the tenant having ≥2 entities.
      **Trigger:** the first multi-entity tenant.

### Surfaced by the round-22 parallel round (2026-09-05)

Found while closing the above. None is a defect that can bite today.

- [ ] **(b) The two `/organization` follow-ups the SSO agent correctly stopped
      at are DONE, but a third remains: the void's card-cancel outcome is
      invisible.** `_cancel_card_for_void` is best-effort and its `card_outcome`
      lands only on the `payment.voided` audit row, never in the response — so
      after voiding a card payment an operator cannot tell whether the card was
      actually closed at the provider, and a failed leg leaves a live,
      bearer-spendable card with no reachable remedy. This is why round 22
      declined to ship a standalone card-cancel control
      ([decisions.md](decisions.md) §96) — the remedy belongs on the void, not
      beside it. **Durable fix:** surface `card_outcome` on `PaymentResponse`
      and in the void dialog, with a retry when the provider leg failed.
      **Trigger:** the next virtual-card slice.

- [ ] **(c) `RebateResponse` carries no currency.** `card_rebates` has no
      currency column; a rebate's currency is knowable only through its card.
      The list envelope's `excluded_rebate_count` lets the UI prove the common
      case (zero ⇒ every row is in the declared currency), but on a
      mixed-currency programme the remaining rows render **bare figures with no
      code**, which is the honest rendering and not a good one.
      **Durable fix:** resolve `currency` from the joined card onto
      `RebateResponse`. **Trigger:** the first tenant running cards in two
      currencies.

- [ ] **(c) `GET /api/cards/rebates` and `GET /api/inspections` are
      unpaginated.** Both are fine at current volumes and both back a table that
      will grow monotonically. `GET /api/inspections` additionally returns
      `gr_id` with no `gr_number` and no `?gr_id=` filter, so the UI carries a
      `page_size=100` receipts fetch purely to label a column.
      **Durable fix:** the Load-More footer + `/ids` resolver pattern the
      invoice, vendor and payment-queue lists already use; plus a `gr_id` filter.
      **Trigger:** either list passing a few hundred rows.

- [ ] **(c) `nav.ts` hides `/goods-receipts` from an `ap_clerk` whose backend
      reads are open to all four roles.** The receipts *and* inspections list
      endpoints are `get_current_user`-gated, but the whole Procurement nav
      group is restricted to admin/ap_manager/cfo, so a clerk has no link to a
      page they are allowed to read. Pre-existing and about the group, not about
      inspections — which is why round 22 left the shared file alone rather than
      widening it as a side effect. **Durable fix:** a product call on whether
      clerks see Procurement, then align `nav.ts` with the route gates either
      way. **Trigger:** a product decision.

- [ ] **(c) Refusal sentences from the e-invoice validator are no longer
      localized.** Deliberate, and a net gain — the deleted client map covered 4
      codes and 12 field paths out of dozens, so most rows previously rendered a
      bare rule id with no explanation ([decisions.md](decisions.md) §95). The
      wrapper copy stays localized. **Durable fix:** a code→`MessageKey` map
      generated from the backend's own rule set, so the catalogue cannot drift
      from the validator. **Trigger:** a localization pass on error content.

- [ ] **(c) The `/adaptive` and inspections surfaces have no mobile
      counterpart.** Recorded so the docs stop claiming "no UI" generally when
      what they mean is "no mobile UI".
      **Durable fix:** mobile screens if either capability is marketed on
      mobile. **Trigger:** a mobile scope decision.

### Surfaced by the round-23 hunt (2026-09-06)

Round 23 ran six read-only hunters alongside four fix agents. The tenant-isolation
and authz audit came back **clean** across every surface rounds 20-22 added
(including the `Host`-resolved SSO fallback — a forged `Host` can only select a
tenant that registered it, and the state/nonce is minted against the
server-resolved slug). The other five found substantially more than the round
could land. Everything below was verified at the code level; nothing here is a
suspicion.

**Fixed in round 23** and not repeated below: audit rows on the three
destructive deletes that had none (invoice hard-delete + its cascade, portal
credential revocation, workflow-definition delete taking its version history);
the `/admin/partner` cross-tenant branding race; the `/audit` CSV export that
exported the live form rather than the query on screen; and nine documentation
statements that contradicted the code.

#### ⚠️ Segregation-of-duties — HELD FOR SECURITY REVIEW

Both are SOC 2 CC6.3 controls. Changing who may approve what is a control-design
decision, not a bug fix, so they are recorded rather than patched. **Loop in the
CISO / Security Analyst before acting.**

- [ ] **(c) One `ap_manager` can complete a vendor bank redirect end to end,
      defeating the BEC dual control.** Verified chain: `POST /vendors/{id}/portal-users`
      is `require_roles(ADMIN, AP_MANAGER)` and returns the plaintext
      `temp_password` for a **caller-supplied** email → logging into that portal
      and staging a bank change writes `requested_by_vendor_user_id`, leaving
      `requested_by_user_id` **NULL** → `approve_change_request`
      (`api/vendors.py`) skips its segregation check entirely when that column
      is NULL → `ROLE_AP_MANAGER` holds `vendor.manage`,
      `vendor.bank_change.approve` **and** `payment.execute`. The code comment
      states the assumption that fails: *"Portal-submitted requests have no AP
      requester, so this only bites AP-initiated ones"* — true only if AP cannot
      create portal identities, and it can. The compensating `fraud_flag` is not
      a second control against the same actor: exception resolution has no
      segregation check either.
      **Durable fix:** record the AP actor who provisions or resets a
      `VendorUser` credential; refuse approval when that actor is the approver;
      stop returning `temp_password` to a caller-supplied address.
      **Trigger:** security review. This is the highest-severity open item in
      this file.

- [ ] **(c) `POST /api/invoices/import-csv` leaves `uploaded_by_id` NULL, so the
      importer can self-approve.** `violates_segregation` returns **False** when
      `uploaded_by_id is None` (`services/approval_chain.py`), and
      `services/csv_import.py` never sets it. The same actor doing the same
      thing through `POST /api/invoices` gets a 403 — manual create stamps the
      field with a comment naming this exact failure. Three places assert the
      control that is a no-op here, including `backend/docs/csv-import.md` ("gets
      a real audit trail, segregation check, and approval signature").
      **Durable fix:** thread the user through and stamp it, plus an
      import-then-self-approve regression test. (`recurring_invoices` and
      `intercompany` have the same NULL uploader but no creator column to fix
      with — a separate, larger question.)
      **Trigger:** security review, alongside the entry above.

#### Money path — verified, unfixed

- [x] **DONE (PR #374).** `/payments/runs/{id}/execute` and `/resume` skipped
      two of the four gates run creation applies. Creation checks payable
      status, financial integrity (`blocking_exception_types`), credit-memo
      netting and the live card claim; the dispatch leg (`_execute_single_payment`)
      re-checked payable status + credit-memo netting but NOT the blocking
      exception or the card claim. So a `fraud_flag` raised **after** a draft run
      exists — exactly what an approved vendor bank change raises ("Vendor bank
      details changed; verify before payment"), while a draft run sits for days
      awaiting CFO sign-off and `_execute_single_payment` re-reads
      `Vendor.bank_details` — did not stop execution; a virtual card minted after
      the run was built was the second variant. `_execute_single_payment` now
      runs `blocking_exception_types` + `card_claimed_invoice_ids` (the SAME
      shared predicates the run builder and `/retry-failed` use) right after the
      payable-status re-check, refusing BEFORE the adapter call with named
      retry-safe reasons `invoice_blocked:<type>` / `invoice_has_live_card`. Also
      covers `/compliance/release`, which flows through the same chokepoint.
      Dispatch-time tests added to `test_payment_run_blocking_exceptions.py` +
      `test_payment_run_live_card_claim.py`.

- [x] **DONE (PR #375).** The ERP webhook was a writer of
      `payment_scheduled → paid` that bypassed the settlement-coverage hold:
      `api/erp_webhook.py` transitioned on `VALID_TRANSITIONS` alone and never
      read `settled_amount` / `settlement_coverage`, so a validly-signed
      `{"status":"Paid"}` from the tenant's own ERP flipped a short-settled
      invoice to `paid` (firing `payment.settled`, emailing the supplier,
      counting the full amount in aging / 1099 YTD) while `/settlement/accept`
      then 409'd — the documented exit gone. The webhook's
      `payment_scheduled → paid` branch now runs the SAME `settlement_coverage`
      check off the SAME persisted `Payment.settled_amount` and, on a
      non-covering verdict, opens an `erp_reconciliation` exception (dedup'd) and
      leaves the invoice at `payment_scheduled` — a silent 204, same two exits
      (`/settlement/accept` or `/void`). `backend/docs/payments.md` corrected
      ("primary code path", not "only").

- [x] **DONE (PR #373).** `derive_run_status` failed open — a run of voided
      payments reported `completed`. `voided` matched none of the four bucket
      tuples so it bumped `total` only and every branch fell through to
      `return "completed"` (with `payments_completed: 0` beside the full
      `total_amount`). Fixed both halves: `voided` joins
      `RUN_PAYMENT_FAILED_STATUSES` (it is a non-success terminal, grouped with
      `cancelled` in `LIVE_PAYMENT_TERMINAL_STATUSES`), and the final rung is now
      fail-closed — `"completed"` only when `completed == total`, otherwise
      `partial` / `failed` — so any future adapter status a bucket doesn't
      recognise can't report success either. Parametrized cases for both in
      `test_payment_run_status_derivation.py`.

- [x] **DONE (PR #377).** The payment queue offered — and counted as "ready to
      pay" — rows the run builder hard-409s. The three queue queries excluded
      only `Payment.status == "completed"`, so an invoice with a `submitted` /
      `processing` / `pending` / `pending_compliance` payment (every real rail —
      ACH settles in 1-3 days) was a selectable queue row that
      `create_payment_run_for_invoices` then 409'd on
      `uq_payments_one_live_per_invoice`, taking the whole select-all batch down
      with no way to bisect. The queue's `_queue_base_where()` now excludes any
      invoice with a **LIVE** payment via `_live_payment_invoice_ids()` — the
      SAME `Payment.status NOT IN LIVE_PAYMENT_TERMINAL_STATUSES` definition the
      run builder's `_live_payment_invoice_numbers` guard uses — applied to
      `/queue`, `/queue/ids`, the rollup KPIs and `/summary`'s `queue_count`. A
      terminal (`failed` / `voided`) payment still leaves the invoice offered
      (re-pay after a failure). New non-tautological drift guard compares the
      offered set against BOTH run-builder refusal predicates.

- [ ] **(c) Four smaller money-reporting defects, each verified.** (a) Discount
      **expiry** is gated behind the auto-capture kill switch (default off), so
      lapsed offers stay `offered` forever and `capture_rate_pct` can read
      `100.00` on 1 captured of 10 — while the config comment and the docs both
      claim only auto-capture is gated. (b)
      `/vendor-statements/summary`'s `open_discrepancies` sums import-time
      counters that `resolve_line` never updates, so it is monotonically
      non-decreasing and counts a class `_ACTIONABLE_CLASSES` deliberately
      excludes. (c) Realized FX gain/loss is recorded on the webhook completion
      path only, so every cross-currency payment the **reconciler backstop**
      recovers is missing its realized-FX rows. (d) `_build_opportunity`
      collapses "unknown horizon" into `annualized_return: 0.00` →
      `worthwhile: false` in the same object as a positive `net_benefit`, so a
      bulk-negotiated offer with no `valid_until` is permanently
      unrecommendable.
      **Trigger:** the relevant slice in each case.

- [ ] **(c) Three latent money hazards.** `payments.home_currency` is `.upper()`d
      but not `.strip()`ed at the one site deciding the FX leg, while its two
      siblings strip — a trailing space routes every domestic payment through
      `international_wire`. A JSON `NaN` settlement amount raises
      `InvalidOperation` in the tolerance comparison *before* reaching
      `fits_numeric`, which handles NaN explicitly (retry storm, not a wrong
      figure). And half-cent rounding disagrees between a `PaymentSchedule` row
      (Python `ROUND_HALF_EVEN`) and its own rollup (Postgres half-away-from-zero)
      — latent only because nothing but `scripts/seed.py` constructs one.

#### Test-cover gaps — the guards do not bite

- [ ] **(c) Tenant isolation has no drift guard at all.** The highest-blast-radius
      invariant in the project is asserted nowhere: `test_tenant_isolation.py`
      proves `get_tenant` cross-checks the JWT org claim *when used*, and nothing
      proves it is used. There are 15 `create_async_engine` sites under `app/`
      outside `app/database.py` — all correct today, all passing
      `_make_tenant_url(org.db_name)` off a resolved row — and nothing
      distinguishes that from a new site interpolating a request-supplied slug.
      `.claude/hooks/security-patterns.sh` has no rule for it either.
      **Durable fix:** an AST scan (the shape `test_pdf_render_offloaded.py`
      already uses) asserting engines are constructed only in `app/database.py`
      or a named exemption whose argument is a `_make_tenant_url(...)` call, and
      that no `^feoh_` literal exists outside `config.py`. DB-free, sub-second.
      **Trigger:** next test-hardening pass — this is the most valuable item in
      this section.

- [ ] **(c) The auth guard is satisfied by a function NAME, with a demonstrated
      bypass.** `test_rbac.py` accepts any dependency whose closure is named
      `checker` — the house style for all five factories in `deps.py`, including
      the two *billing* gates. And `NO_AUTH_REQUIRED` makes zero assertions once
      an entry is listed: **deleting `Depends(get_scim_tenant)` from all six SCIM
      `/Groups` handlers — a credential that creates accounts and grants roles —
      leaves the suite green.** Two allowlist entries
      (`POST /auth/forgot-password`, `POST /auth/reset-password`) are documented
      nowhere as public-by-design.
      **Durable fix:** resolve auth deps by identity through `route.dependant`
      (the walk exists in `test_sod_endpoint_wiring.py`), and split the allowlist
      into `PUBLIC_BY_DESIGN` (driven with a real credential-free request) and
      `ALTERNATE_AUTH` (asserted present in the tree).

- [ ] **(c) The audit sweep is per-MODULE, not per-handler**, which is how the
      three unaudited deletes round 23 fixed went unnoticed: `api/invoices.py`
      has 21 tenant-mutating routes and one auditing handler exempted the other
      twenty. **Durable fix:** switch the unit to `inspect.getsource(endpoint)`
      with a same-module helper-call allowance and a handler-keyed exemption
      dict.

- [ ] **(c) The money-is-`Numeric` sweep's token list has drifted off the
      schema.** It matches `("amount","total","price","subtotal","_tax","amount_")`;
      **35 `Numeric` columns do not match**, including every FX rate at
      `NUMERIC(18,8)` and the expense-policy thresholds. A new `Column(Float)`
      named `balance`, `budget`, `limit`, `fee`, `cost` or `*_rate` passes the
      whole file, and the sweep only asserts "not Float" — a money column typed
      `String` passes too. **Durable fix:** invert it — every `Numeric` must
      carry precision+scale, and no `Float`/`Double`/`REAL` may exist outside a
      named non-money allowlist, so new columns are opt-out rather than opt-in.

- [ ] **(c) One guard scans 1 route out of 564.** `test_audit_append_only.py`'s
      naive `for route in app.routes` sees a single `APIRoute` under FastAPI
      0.138+, so its audit-path filter yields `[]` and it passes having examined
      nothing. Its own next test documents this exact behaviour and uses
      `iter_route_contexts`. Currently redundant (a sibling covers the same
      ground) but will silently keep reporting green the day that sibling is
      relaxed. **Durable fix:** one-line swap to `iter_route_contexts`.

- [ ] **(c) The SoD wiring pin omits the routes that actually move money.**
      `test_sod_endpoint_wiring.py` hand-lists 19 routes; `execute`, `void`,
      `settlement/accept`, `compliance/release`, `compliance/dismiss`, `resume`,
      `sync-erp` and `retry-failed` are not among them, and two of the eight
      catalogue permissions are never imported. All are correctly gated **today**
      — verified — so this is coverage, not a live defect, on the one class of
      route where the SoD layer existing at all is the point.

#### Performance — measured, unfixed

Numbers from a 200k-invoice / 1M-audit-row scratch database, medians of 5-7 warm
`EXPLAIN (ANALYZE, BUFFERS)` runs, dimensions randomised independently (the
round-20 lesson about correlated generators was applied).

- [ ] **(c) `GET /api/dashboard` streams the whole invoice table into Python —
      591 ms at 190k invoices, 361 ms of it a synchronous fold on the event
      loop.** The top-vendor tile selects five columns of every non-rejected
      invoice, all time, no LIMIT, then folds in Python — while the rollup,
      pipeline, aging and trend blocks immediately around it all `GROUP BY` in
      SQL, and the comment above them says to. The equivalent `GROUP BY … LIMIT
      10` measures **20.6 ms — 29× faster**, and linear in invoice count either
      way. The Python fold is also a blocking CPU loop in an `async def`, the
      shape project invariant *"blocking work does not run on the event loop"*
      forbids, in a form none of the five existing drift guards covers.
      **Durable fix:** group in SQL; the `_rep_expr` CASE it needs is already in
      scope. **Trigger:** next dashboard slice — this is the landing page.

- [ ] **(c) `audit_log` has no index on `created_at`, so the shipper seq-scans
      the whole table every 60 s forever.** At 1M rows the caught-up query
      (`shipped_at IS NULL ORDER BY created_at LIMIT 500`) costs **31.9 ms /
      25 054 buffers** and returns nothing; with a partial index
      `(created_at) WHERE shipped_at IS NULL` it is **0.020 ms / 1 buffer** — a
      **1 600×** win, and the index is **8 KB** because it only holds the
      unshipped tail. Per tenant, every minute, on a healthy platform.
      `audit_log` is the fastest-growing table in the schema.
      **Durable fix:** three indexes (that partial one, `(action, created_at)`
      for the signature sweep, `(created_at)` for the export).

- [ ] **(c) `invoices` has no index covering the list's own sort order.** One
      page-1 view costs ~19 000 buffers at 200k invoices (list 15.9 ms, count
      10.5 ms, chips 15.3 ms); with `(created_at DESC, id DESC)` and
      `(status, created_at DESC, id DESC)` the list is **0.060 ms / 23 buffers**.
      Plans go bitmap-heap + top-N heapsort → index scan, and the fixed version
      stays flat as the table grows. Measured **not** to help: the `/counts`
      chips (a rollup over 100% of the table — correctly a seq scan, same
      conclusion as round 20's budget rollup) and the `search=` path (leading
      wildcard; only `pg_trgm` would serve it, with its own write cost). The same
      shape holds on `payments`, `exceptions`, `expenses`, `purchase_orders`,
      `contracts` and `corporate_card_transactions`.

- [ ] **(c) N+1 on `GET /api/vendors` and two siblings, and an unbounded audit
      export.** The N+1s are bounded by page size and cheap on the DB
      (index-only, 0.11 ms each) but cost **~100 ms of pure round-trip latency**
      at page_size 100 against a 1 ms-RTT database; the fix is one grouped
      query, one batched `IN`, and a `DISTINCT ON`. Separately `GET /api/audit`
      materialises every row in the range as ORM objects with no LIMIT — 41 649
      rows for 30 days on the scratch DB, a whole-table heap for an annual SOX
      export — while its own sibling `verify-signatures` already streams with
      `yield_per=500`.

#### Frontend — verified defects

- [ ] **(c) A `Payment` carries no currency on the wire.** `PaymentResponse` and
      `PaymentRunResponse` join `invoice.vendor_name` but not `invoice.currency`,
      so six `/payments` call sites fall back to the org default. Worst in the
      **Accept settlement** dialog: "Authorized" renders `$1,200.00` (fabricated)
      directly above "Settled" `€1,150.00` (real, from `settled_currency`) — the
      two figures that dialog exists to compare, in the screen built to catch
      `currency_mismatch`. The e2e asserts only the digits, so the suite cannot
      see it. Same class one screen earlier: the create-run review panel drops
      the `QueueItem.currency` the queue row above it renders.
      **Durable fix:** add `currency` to both response models (the invoice row is
      already joined) and thread it through. **Trigger:** next payments slice.

- [ ] **(c) Four modal fetches skip the request-identity guard**, after round 23
      fixed the one that writes. `/vendors/screening` **acts** on the wrong row
      (vendor B's modal showing vendor A's sanctions timeline, with Block/Unblock
      operating on B) in a file that already uses sequencers for its list and
      counts; `/audit`'s signature drill-down, `/admin/api-keys` and
      `/experiments` are read-only. The convention and its shared primitive are
      documented on `/goods-receipts`.

- [ ] **(c) Two surfaces answer "we could not look" with "there is nothing".**
      `/vendors/screening`'s history modal renders "No screening history yet."
      on a failed fetch, directly above the Block/Unblock control whose decision
      rests on that timeline; the `/payments` Cards tab is the only one of five
      with no `*Errored` flag at all. `/exceptions` states the rule verbatim:
      *"never reintroduce an empty message that outranks 'we could not look'"*.

- [ ] **(c) `/invoices` deep-link and `/adaptive` presentation gaps.** The
      deep-link re-open race was fixed in round 23; what remains is that
      `/invoices` has **two URL writers** (`syncUrl` and `syncSortUrl`) that each
      rebuild from `$page.url` and can clobber each other's params (observed
      once as a lost `sort=`), and on `/adaptive`: anomaly rows show a truncated
      invoice id that can be neither opened nor pasted anywhere; an unconvertible
      amount is labelled with the org's reporting currency (the backend
      deliberately falls back to the *billed* figure "for DISPLAY only" and emits
      no currency); the only `info`-severity flag renders as a **warning**; and
      the vendor average excludes rows the sample count includes without
      disclosing `unconverted_count` — the rule §79 decided and §82 reinforced.

- [ ] **(c) WCAG 2.2 AA 2.5.8 target-size on row checkboxes.** `input[type=checkbox]`
      is globally 16×16 in `app.css` with ~2.2px clear space to the neighbouring
      row link; axe reports `[serious] target-size` on `/invoices` whenever the
      first row is selectable. Masked in CI only because `a11y/` sorts before
      `invoices/`. The fix is global and touches every table, so it wants its own
      slice rather than a ten-agent round.

- [ ] **(c) 13 more hardcoded English status-label maps.** Round 23 localized
      `INTAKE_STATUS_LABELS` and `REQUISITION_STATUS_LABELS`; the same shape
      remains in `invoice.ts`, `payment.ts`, `contract.ts`, `experiments.ts`,
      `expense.ts` (×4), `vendor.ts`, `positivePay.ts`, `vendorStatementRecon.ts`,
      `catalog.ts` and `recurring.ts` (×2) — roughly 70 keys × 6 locales,
      mechanically identical to what landed.

- [ ] **(c) Three e2e specs leak rows into the shared worker tenant**
      (`create-manual`, `line-total-reconciliation`, and the vendors ones round 23
      fixed), which is what surfaced the two intermittent failures above. Also
      `EntitySwitcher` lists **inactive** entities, so an entity deactivated on
      the new `/admin/entities` page stays selectable and new rows land under it.

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

**Written — CLOSED (round 20).**
[`docs/founder-runbooks/custom-domain-provisioning.md`](founder-runbooks/custom-domain-provisioning.md)
covers both deployment shapes, because the certificate story differs between
them: Caddy + Let's Encrypt HTTP-01 on the single VM (per-host site block,
automatic renewal, no separate validation record) and ACM in `us-east-1` behind
CloudFront alternate domain names (a permanent `_token` CNAME the customer owns;
certs are immutable, so onboarding re-issues). Plus the DNS records, the CORS
env change, end-to-end verification through the public branding endpoint,
rollback, and the failure modes this code path actually has.

The whole AWS branch and every quota figure are marked confirm-on-first-run:
`infra/` is KMS + S3 only, so no distribution or certificate exists yet, and no
ARN or resource name was invented.

**Restored, without the credential that made it inert — CLOSED (round 20).**
`.github/workflows/dependabot-lockfile.yml` is back. The reason it was removed
in #325 is designed out rather than re-gated: it no longer needs a
`DEPENDABOT_LOCKFILE_PAT`, so there is no missing-secret gate left to
skip-and-succeed. It runs on `pull_request_target` (base-branch context, so
`GITHUB_TOKEN` can carry `contents: write`), filtered to the four manifests and
gated on `github.actor == 'dependabot[bot]'` plus a same-repo head — strictly
less privileged than `dependabot-auto-merge.yml`, which already merges these
PRs on the same trigger.

Three jobs, and only one holds `contents: write`. The two resolver jobs check
out PR head **by immutable SHA** with `persist-credentials: false`, reference no
secret, and run the resolvers — sized on the assumption that `uv pip compile`
can build an sdist and execute third-party `setup.py`. The push job downloads
their artifacts and runs only `git` and `cp`, takes every trusted input from the
event payload, uses no `--force`, and refuses if the branch tip moved off the
SHA the resolvers ran against.

**The pnpm overrides check is the point of the workflow, not a detail.** Between
`--lockfile-only` and the upload it asserts every `pnpm.overrides` key is
present in the lockfile at the same value; a miss exits 1 *before* anything is
uploaded, so the push job has nothing to commit. There is no
`--no-frozen-lockfile` anywhere in the file — the automation must never become
the thing that quietly launders away the guard that caught #344/#351. That check
was extracted and run against the real files: passing intact, failing on a
deleted block, a dropped key and a weakened value.

**Two things remain unverified and are not claimed.** Whether the trigger fires
and whether the token's push to a Dependabot branch is accepted both need a real
Dependabot run. And a `GITHUB_TOKEN` push starts no new workflow run, so the
synced commit is *unverified rather than green* — closing that needs the same
PAT/GitHub-App operator step, which this design deliberately does not depend on.
The manual recipe in [backend/CLAUDE.md](../backend/CLAUDE.md) § Dependency lock
stays the documented fallback.
**Trigger for the remaining verification:** the next Dependabot manifest bump.

