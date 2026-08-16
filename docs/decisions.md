# Architecture decisions

Short records of non-obvious choices that the code alone doesn't explain. Reach
for this before proposing something that's already been considered and rejected.

This is not a strict ADR template — each entry is a few paragraphs: what we
decided, why, and what we traded away. **Append new entries to the bottom; don't
rewrite history.** Number them sequentially so other docs, commit messages, and
code comments can cite them as `decisions §N`. Date an entry when you know the
date (a migration's landing date is a good anchor); leave it off rather than
guess.

Entries are deliberately short. Where a full treatment already exists in
`docs/` or `backend/docs/`, this file records **the call and the reasoning** and
links out for the mechanics — it does not duplicate them.

**Scope:** decisions, not status. Feature progress lives in
[roadmap.md](roadmap.md), open deferrals in [followups.md](followups.md),
diagnosed-but-unfixed defects in [known-issues.md](known-issues.md).

---

## 1. Database-per-tenant, with a JWT cross-check as the real boundary

**Decided:** initial architecture · `backend/app/tenant.py`

Each tenant gets its own PostgreSQL database (`feoh_<slug>`); a shared
control-plane DB (`feohledger`) holds organizations, users, and roles. The
alternative — one database with a `tenant_id` column and row-level security —
was rejected because an AP ledger's blast radius on a missed predicate is
somebody else's invoices and bank details, and a forgotten `WHERE tenant_id =`
is a silent, ordinary-looking bug.

The decision that actually carries the security, though, is **not** the physical
split — it's that `get_tenant()` cross-checks the JWT's `org` claim against the
org resolved from the `X-Tenant-Slug` header and 403s on mismatch. Without that,
database-per-tenant buys nothing: an authenticated user from tenant A could read
tenant B simply by changing a header. The same cross-check gates the white-label
custom-domain fallback, so a forged `Host` can't widen access either.

**Trade-off:** every schema change has to fan out across every tenant DB
(`scripts/migrate_all_tenants.py`), connection-pool pressure scales with tenant
count (hence `pool_size=1, max_overflow=0` on the worker engines), and
cross-tenant analytics are not a single query. Accepted.

**Don't re-litigate unless** tenant count reaches a scale where per-tenant
connections, not correctness, is the binding constraint.

See [multi-tenancy.md](multi-tenancy.md).

---

## 2. The invoice header `amount` is never recomputed from its line items

**Decided:** `backend/app/services/invoice_warnings.py`

`PUT /api/invoices/{id}/line-items` does **not** set
`invoice.amount = sum(lines)`. Three reasons, any one sufficient:

- **Line `total` semantics aren't uniform across ingest paths.** The vision
  adapters emit a *tax-inclusive* line total; `e_invoice/mapper.py` maps the same
  column onto UBL `LineExtensionAmount`, which is *tax-exclusive*. An overwrite is
  right under one reading and wrong under the other.
- **Lines are frequently partial** — a reviewer keys only the disputed line.
- **It would move money with no approval behind it.** `amount` is a
  `_FINANCIAL_FIELDS` member; the header `PATCH` refuses to touch it once
  approved, and the approval signature is taken over the exact amount. A side
  effect of a line edit must not do what the header edit itself is forbidden to
  do.

Equally, a mismatch is **not** a 422 — `tax_amount` / `shipping_amount` /
`discount_amount` are separate header columns, so `sum(lines) != amount` is the
ordinary shape of a valid invoice. Instead the divergence raises an `error`-level
`line_total_mismatch` warning plus a de-duplicated exception, which blocks
payment-run inclusion until a human resolves it.

**Trade-off:** a genuinely wrong header stays wrong until someone acts on the
exception. That's the intended shape — a human, not a sum, decides.

See [line-total-reconciliation.md](../backend/docs/line-total-reconciliation.md).

---

## 3. Card-rail payments are excluded from the 1099 reportable total

**Decided:** `backend/app/services/payment_methods.py`

The IRS puts card-settled payments on Form 1099-K, filed by the *settlement
entity*, not on the payer's 1099-NEC/MISC. So a vendor paid $10,000 by ACH and
$5,000 by virtual card gets a **$10,000** box-1 figure. Filing $15,000
over-reports the vendor and double-counts the card leg against the processor's
own 1099-K.

Two sub-decisions worth recording:

- **Unknown / `NULL` rails stay reportable.** Defaulting an unclassified rail
  *out* would silently drop filed money; under-reporting is as wrong as
  over-reporting. The manual/legacy payment path writes NULL, so this is not
  hypothetical.
- **The excluded money is surfaced, not dropped.** `card_paid` /
  `total_card_excluded` appear on the report so the difference reconciles.

The classification lives in exactly one module, and
`tests/test_payment_methods.py` is a **drift guard** — adding a rail to the
`PaymentMethod` enum, to any adapter's `supported_methods`, or to the corridor
fee overrides fails the suite until that rail is classified.

See [tax-1099.md § Card payments are excluded](../backend/docs/tax-1099.md).

---

## 4. A NULL `entity_id` means "unstamped" on vendors but "shared" on GL accounts

**Decided:** multi-entity Phase 1–3 · `backend/app/services/vendor_matching.py`

The same NULL carries two different meanings on purpose, which is the kind of
thing that looks like a bug on first read.

On `gl_accounts`, NULL is a deliberate **shared chart** marker — one chart of
accounts serving every subsidiary. On `vendors`, NULL means the row was never
stamped with a subsidiary: a pre-multi-entity row migration `0029`'s backfill
didn't reach, or one auto-created from an entity-less invoice.

Unstamped vendors stay matchable from **every** entity. Excluding them would not
fail loudly — it would silently mint a duplicate vendor, splitting the supplier's
spend rollup and giving it a second, independently editable bank-detail record.
A supplier is a real-world counterparty, not subsidiary-private data. When the
same supplier exists both unstamped and under the invoice's own entity, the
entity's own row wins.

The scoping matters because `Invoice.vendor_id` is what the fail-closed
credit-memo guard (§9) compares — a cross-entity mislink has a money consequence.

**Trade-off:** the asymmetry needs explaining every time someone reads
`apply_entity_scope(include_shared=True)`. Hence this entry.

See [vendor-management.md § Matching is scoped to the invoice's entity](../backend/docs/vendor-management.md).

---

## 5. Custom roles granted no access at all until a real permission layer existed

**Decided:** superseded 2026-06-20 by migration `0062_role_permissions`

Custom roles were creatable in the UI long before they could grant anything —
`require_roles` matched system role names only, so a custom role was inert by
design. This was repeatedly mistaken for a bug, and the tempting "fix" was to
make `require_roles` match custom role names too.

That was rejected. Role-name matching would have made a custom role an
all-or-nothing clone of a system role, which is the opposite of what an org
splitting duties needs — and it would have let an admin mint a role that silently
carried payment-execution rights.

The durable fix shipped instead: a granular permission catalog
(`backend/app/api/permissions.py`), a static system-role → permissions map
reproducing the prior matrix exactly, and a control-plane `roles.permissions`
JSONB column. Effective permissions are the union over a user's roles. Only the
*splittable* fraud-sensitive duties moved to `require_permission` — payment
execute/void, run approve, vendor bank-change approve, vendor block/manage, user
management. Everything else stays on `require_roles`.

**Trade-off:** two enforcement mechanisms coexist. Deliberate — moving
everything to permissions would have been a large, low-value blast radius.

See [authentication.md § Granular permissions](authentication.md).

---

## 6. Vendor-statement differences are reconciliation lines, not Exceptions

**Decided:** migration `0047` · `backend/app/services/vendor_statement_recon.py`

A `missing_on_our_side` row — "the supplier billed invoice X and we have no
invoice for it" — has, by definition, no invoice on our side. When
`Exception.invoice_id` was still `NOT NULL`, representing it as an Exception
would have meant fabricating a placeholder invoice. Migration `0049`
(2026-06-19) later made that column nullable for Positive Pay's invoice-less
fraud flags, so the constraint is no longer the blocker — but the recon line
remains right regardless:

- It **describes a missing invoice and feeds intake**; the clerk resolves the
  line by creating the real invoice.
- **The run is the unit of work, not the invoice.** Exceptions hang off one
  invoice; a statement reconciliation is a vendor-and-period batch whose lines
  roll up together into counts, totals, and close-readiness.
- **Different lifecycle** — resolve/ignore with a note, and the run auto-flips to
  `resolved` once no actionable line is open.

**Trade-off:** two review queues for a clerk to watch. Accepted; they answer
different questions.

See [vendor-statement-reconciliation.md](../backend/docs/vendor-statement-reconciliation.md).

---

## 7. API keys are unsalted SHA-256 + an indexed prefix, not bcrypt

**Decided:** 2026-06-19 · migration `0055_api_keys`

This deliberately departs from the project invariant that says passwords use the
shared `bcrypt_sha256` context — and the departure is narrow and reasoned, so a
reviewer shouldn't flag it.

API keys are high-entropy random tokens (`secrets.token_urlsafe(32)`), not
user-chosen passwords, and they must be **looked up** by the presented value. A
salted bcrypt hash is deliberately un-indexable: verifying would mean scanning
every row and bcrypt-verifying each. So the platform stores `sha256(full_key)`
plus an indexed `key_prefix`, resolves candidates by prefix, then compares in
constant time via `hmac.compare_digest`. Brute-forcing a 256-bit random token is
infeasible, so salting buys nothing here.

This is the same pattern the SCIM bearer token already used
(`Organization.scim_bearer_hash`). The `bcrypt_sha256` invariant remains
absolute on the **password** path.

**Trade-off:** two hashing idioms in the codebase. Mitigated by code comments at
both sites plus this entry.

See [public-api.md § Why API keys are SHA-256, not bcrypt](../backend/docs/public-api.md).

---

## 8. A candidate MFA secret lives in Redis, never on the account row

**Decided:** `backend/app/api/auth.py` · `FEOH_MFA_ENROLL_PENDING_TTL_SECONDS`

`POST /auth/mfa/enroll` mints a *candidate* TOTP secret and parks it in Redis
(`mfa:pending_enroll:<user_id>`, 15 min default). `User.mfa_secret` /
`mfa_enabled` / `mfa_enrolled_at` are written by `/auth/mfa/enroll/verify` and
nowhere else.

Previously enroll-start wrote the new secret straight onto the row and cleared
`mfa_enabled` — which made merely *starting* an enrollment a silent
second-factor strip. An abandoned enrollment left the account single-factor with
no signal to anyone.

Two related calls in the same area:

- **Changing an existing factor is a step-up**, gated by password, a code from
  the live authenticator, or a WebAuthn assertion — required only once a factor
  exists, so first-time enrollment stays frictionless. Removing a factor is
  gated exactly like adding one.
- **Step-up challenges are bound to purpose *and* operation** via separate
  single-use Redis slots, so a step-up assertion can't be replayed as a login, a
  login assertion can't satisfy a step-up, and a register assertion can't
  authorize a delete.

The supplier portal mirrors the pending-enroll behaviour
(`mfa:vendor_pending_enroll:`) but has **no** passkey step-up —
`WebAuthnCredential` is control-plane and `VendorUser` is tenant-scoped.

See [authentication.md § Per-user enrollment](authentication.md).

---

## 9. Applying a credit memo is fail-closed on the vendor link

**Decided:** `backend/app/api/credit_memos.py`

A credit memo's vendor must **provably** equal `Invoice.vendor_id` before the
memo reduces the invoice's balance. A NULL `Invoice.vendor_id` is refused with a
409 rather than treated as a wildcard match.

Treating NULL as "matches anything" is the natural-looking implementation and it
is exactly the mis-attribution to prevent: an invoice whose vendor can't be
established must not have money moved against it on a guess. Both application
paths (`POST` with an `invoice_id`, and `POST {id}/apply`) row-lock the invoice
and run the same vendor / currency / over-application guards.

The corollary is that resolving a legacy unlinked invoice is a **human** action,
not a backfill migration: re-saving the vendor name on `PATCH /invoices/{id}`
re-runs `vendor_matching.match_and_link_vendor`. A backfill would have had to
guess historical vendors — the precise error the guard exists to stop.

See [api-reference.md § Credit Memos](../backend/docs/api-reference.md).

---

## 10. The frontend is static, and stays static

**Decided:** initial architecture · `frontend/svelte.config.js` (adapter-static)

No SSR, no server routes, no SvelteKit endpoints. Every dynamic value comes from
the backend API. This is what lets the app deploy to GitHub Pages with no
runtime, and it makes the trust boundary trivially auditable: the browser bundle
is public, so anything secret-bearing is unambiguously the backend's job.

**Trade-off:** no server-side rendering for SEO or first paint, and the SPA
needs its own subdomain-to-tenant resolution (`src/lib/tenant.ts`) rather than
reading it server-side. Accepted — the app is behind a login; SEO is irrelevant.

**Don't re-litigate unless** a genuinely public marketing surface needs to live
in this repo. Even then, prefer a separate static site over adding an SSR
adapter.

---

## 11. Local-first: every external dependency ships a mock and a safe default

**Decided:** standing rule · root `CLAUDE.md` guard rail 7

`pnpm dev` must run on a laptop with no cloud account and no API key. Every
external integration ships (a) a local equivalent and (b) a committed default
that points at it — Postgres/Redis/MinIO via Compose, `mock` adapters for
extraction / ERP / cards / payments / FX / sanctions / audit shipping /
billing / QMS / enrichment / PEPPOL, `console` for email, Keycloak for SSO,
LocalStack for AWS, fake-erp for the three real ERP adapters.

The consequence people bump into: `backend/.env.development` and
`frontend/.env.development` are **committed**. That's deliberate — they carry
only loopback URLs, mock adapter names, and the `change-me` JWT key. Real
secrets never appear in any `.env*`.

The skeleton adapters (`c2fo`, `dun_bradstreet`, `clearbit`, `complyadvantage`,
`dowjones`, `refinitiv`, `as4_gateway`, `stripe_billing`) all **fail closed**
without a key rather than falling back to a hardcoded default — a fallback would
turn a missing credential into silently wrong data.

**Trade-off:** every new integration costs a mock adapter up front. That cost is
the feature.

---

## 12. Production secrets live in a separate private repo, not in-repo sops

**Decided:** standing rule · root `CLAUDE.md`

This repo is **public**. `bin/sops-init.sh` exists and `backend/.env.sops` /
`infra/terraform.tfvars.sops` are wired, but **no encrypted payload is
committed** and none should be. Committing ciphertext to public history is
permanent: the KMS key can be revoked, but the blob is archived, forked, and
scraped forever, and it hands an attacker an offline target.

Production secrets go to the private estate repo `Absence0760/infra-secrets`
(per-project subdirectory, its own KMS key, IAM-gated `kms:Decrypt`). This is
the pattern `meryl-green-designs` got wrong by committing `*.sops` into its own
public repo; migrating that is tracked in the estate docs.

See `~/github/project-mgmt/docs/secrets-management.md`.

---

## 13. Workflow config is frozen onto each invoice at creation

**Decided:** `backend/app/services/workflow_engine.py`

`WorkflowInstance.steps_config_snapshot` is a copy of the workflow definition
taken when the invoice enters the workflow. In-flight invoices read the
snapshot; editing a definition never affects them.

Without this, an admin tightening an approval threshold would retroactively
change the rules an invoice was already halfway through — an invoice could sit
approved under rules that no longer exist, or become un-approvable mid-flight.
For a SOX-relevant approval trail, "which rules applied to *this* invoice" has to
be answerable years later from the row itself, not reconstructed from the
definition's edit history.

The A/B testing layer depends on the same invariant: `assign_variant` freezes the
chosen variant's config onto the snapshot, so an experiment's arm can't shift
under an invoice mid-run.

**Trade-off:** a definition fix doesn't rescue already-in-flight invoices; they
need an explicit re-route. Correct — that's a decision, not a side effect.

See [workflow-design.md](../backend/docs/workflow-design.md).

---

## 14. Partner/child tenant linking requires two-sided consent

**Decided:** 2026-06-20 · migration `0065_org_parent`

A partner org can administer child tenants' branding, so "attach a child" is an
access-granting operation. The obvious implementation — the partner posts a child
org id — would let any partner adopt any tenant.

Instead: the **child's own admin** mints a single-use HMAC-signed link code
(`POST /partner/link-code`, ~30 min TTL) for its own org, and handing that code
over **is** the consent. The partner's admin redeems it. A forged, cross-key, or
already-used code is an opaque 400/409; re-parenting an org that already has a
different parent is a 409 takeover (same-partner is an idempotent no-op); a
partner cannot self-adopt. Both organizations' audit trails get a row.

`FEOH_PARTNER_LINK_SIGNING_KEY` is the single on/off knob — no key, no attach,
no fallback.

The sibling path, `POST /partner/children/provision`, takes **no parent id at
all**: it stamps `parent_org_id` from the caller, so a partner can only create a
child under itself.

See [white-label.md § Partner / reseller admin](white-label.md).

---

## 15. The report builder's security boundary is a whitelist, not sanitization

**Decided:** 2026-07-27 · migration `0071_report_definitions`

Self-serve reporting means user-supplied query shape, which is the classic SQL
injection surface. The decision is that the client **never sends a SQL fragment,
column name, or table name** — only catalog *keys*.

`report_builder.REPORT_SOURCES` is a hardcoded map from key → a real,
server-defined SQLAlchemy column, along with the aggregations, filter operators,
and date grains that key may use. `compile_spec` validates every reference
against the catalog **before any SQL is built**; anything unknown raises
`ReportValidationError` → 422 and is never compiled. Filter values bind as
parameters and coerce to the column's Python type.

The point of the ordering — validate, *then* build — is that there is no code
path where an unvalidated identifier reaches the query builder at all. Escaping
or sanitizing an attacker-supplied identifier would be a weaker guarantee for no
gain: the set of legal reports is finite and known.

**Trade-off:** adding a reportable field is a code change, not configuration.
Accepted.

See [report-builder.md § Security model](../backend/docs/report-builder.md).

---

## 16. Invoice file management freezes at `done`, not at `paid`

**Decided:** `backend/app/api/invoices.py`

`PUT`/`DELETE /api/invoices/{id}/file` refuse with 409 once the invoice reaches
`done`. `paid` stays mutable.

The ask was "terminal state", and `paid` is **not** terminal in the state machine
— `payment_scheduled` and `paid` can both void back to `approved`. Freezing the
file at `paid` would have been inconsistent with every other file-adjacent gate
in the router, and would strand a mis-scanned document on an invoice that can
still legitimately re-enter review.

The audit surface was decided at the same time and the supplier chat thread was
**rejected** for it: a file swap is an internal AP action with no reason to be
visible to the vendor. Chat is vendor-facing collaboration. Replace/delete write
`invoice.file_replaced` / `invoice.file_deleted` through the normal
`dispatch_audit` path and render in the Activity timeline like every other
correction.

---

## 17. Concurrent sessions get a git worktree, because the scope guard is path-granular

**Decided:** standing rule · `.claude/settings.json`, `.claude/hooks/git-scope-guard.py`

`git-scope-guard.py` blocks bare `git commit`, `git add -A/.`, and whole-tree
operations, forcing path-scoped commits. In a *shared* checkout that stops a
session committing files it didn't name — but it cannot separate two sessions'
edits to the **same file**, because a path-scoped commit of that file captures
whatever is in the one shared working tree.

So the rule is one worktree per concurrent session (`claude --worktree <name>`),
branched from local `HEAD` (`worktree.baseRef: "head"`, because this repo runs
ahead of an unpushed `origin`). A worktree removes the shared tree entirely,
which is the only real fix. Note that a subagent's `isolation: "worktree"`
isolates *that subagent*, not the top-level session.

The consequence that bites: a worktree commits on its own branch and git won't
let it check out `main`, so that work reaches `main` only via an explicit merge
from the primary checkout — **retiring a worktree does not merge it.** The
`SessionStart` hook `.claude/hooks/unmerged-worktree-check.sh` is the backstop.

A worktree isolates files, **not the database**. Backend `pytest` handles that
itself via per-process realdb slots; sharing the DB with a running dev backend is
still unsafe (see [known-issues.md](known-issues.md)).

---

## 18. The cash-flow copilot narrates deterministic functions; it never computes money

**Decided:** `backend/app/services/cash_flow_plan.py` · `FEOH_CASHFLOW_COPILOT_ENABLED`

The LLM's only job is turning natural language into a typed tool call and
narrating the result. **Every dollar figure comes from an existing deterministic
pure function** — `bucket_outflows`, `compute_cash_position`,
`apply_payment_timing_scenario`, `discount_optimizer.optimize` — serialized as
exact decimal strings, never floats.

That makes answers exact and reproducible under the `mock` adapter, and it means
a model swap can change the prose but not a number. It also draws the audit line
cleanly: there is no path where a generated figure reaches a screen.

`propose_payment_plan` **reuses the discount optimizer's own selection** rather
than re-deriving it, so the plan card and `optimize_discount_capture` can't
disagree. A captured discount that doesn't map cleanly onto a single commitment
row is flagged in `unretimed_offer_ids` rather than silently misrepresenting the
cash curve — surfacing the gap beats a plausible-looking wrong curve.

The hard boundary: **the copilot never moves money.** Its most privileged
intended write (Phase 3, unshipped) is staging a *draft* payment run through the
existing idempotent, CFO-gated path. Funding stays behind human review.

See [cash-flow-copilot.md](cash-flow-copilot.md).

---

## 19. The roadmap is split into open work and a shipped archive, and pruned on landing

**Decided:** 2026-08-06

`roadmap.md` had grown to 1131 lines of which roughly 95 % described work
already finished. Two consequences, both observed rather than hypothetical:

- **Answering "what's left?" required grepping** for non-`Done` statuses and
  unchecked boxes rather than reading the file.
- **Stale statuses survived unnoticed.** Expense Management still read "In
  progress (foundation shipped — WF1)" with every checkbox ticked through WF4;
  the Public Developer API and Supplier Portal entries were similarly out of
  date. Nobody was going to spot that inside a thousand lines of shipped prose.

So: `roadmap.md` keeps only sections with genuinely open work (11 of 51), each
gaining an `**Open:**` line that names what's left and links to
[followups.md](followups.md); `roadmap_shipped.md` takes the other 40
**verbatim**, under their original priority headings.

Two sub-decisions worth recording:

- **The archive is not summarized.** Compressing shipped entries would destroy
  the thing that makes them useful — the checkbox detail is how someone answers
  "does the platform already do X?" before rebuilding it. Size is not the
  problem an archive has.
- **A section moves on its *last* open item landing, not incrementally.** Ticking
  a box mid-section leaves it in the open file. That keeps a section's scope
  readable as one unit and makes the move a single, reviewable diff.

The split criterion is deliberately mechanical: a section stays open **iff** it
has an item in `followups.md`. That makes the two files verify each other — a
section with an `**Open:**` line but no follow-up entry (or the reverse) is a
detectable inconsistency rather than a matter of taste.

**Trade-off:** a priority heading that contains both open and shipped sections
is duplicated across the two files, and moving a section is a manual step the
`doc-hygiene-checker` agent has to remember to flag. Accepted — both are cheaper
than a roadmap nobody trusts.

**Don't re-litigate unless** the open file starts accumulating shipped sections
again, which would mean the prune-on-landing rule isn't being followed — fix the
rule's enforcement, not the split.

---

## 20. A write commits before its response is sent, not from the dependency's teardown

**Decided:** 2026-08-06 · `backend/app/database.py::commit_before_response`

Every session in this app came from a `Depends(yield)` provider that committed
in its post-`yield` teardown. FastAPI unwinds that teardown from an
`AsyncExitStack` it only exits **after** `await response(scope, receive, send)`
— so the client already held its `201` for a write that had not been made
durable. Measured on the real app (instrumenting `AsyncSession.commit` and the
outermost ASGI `send`): both the tenant and control commits landed *after*
`http.response.start`.

For an AP ledger that is a durability defect, not a test annoyance: the API
acknowledged money-relevant writes it had not committed.

**The fix.** FastAPI unwinds a *second*, inner stack (`fastapi_function_astack`)
**before** sending. `commit_before_response` registers the success-path commit
there, so it lands on the correct side of the response. The two session
providers call it; the post-`yield` commit stays as a conditional backstop
(`if session.in_transaction()`), which covers writes made after the response
starts (a streaming body) and any request where the hook could not register.

**Why not the two options the original write-up proposed:**

- *An explicit commit in every mutating handler* touches dozens of files, and
  every future route has to remember. A durability rule enforced by memory is
  not enforced.
- *An ASGI middleware committing before forwarding `http.response.start`* is the
  common workaround, but it needs its own session registry, has to special-case
  requests that never touch the DB, requests using both sessions, and streaming
  responses — and this app already runs a `BaseHTTPMiddleware` whose own task
  semantics interact with send-wrapping. More moving parts for the same result.

A custom `APIRoute` class was also rejected: it only applies to routers that opt
in, so router #66 constructed with a plain `APIRouter` would silently reinstate
the bug. **Silent** reintroduction is worse than the loud alternative below.

**Trade-off:** `fastapi_function_astack` is a FastAPI internal. Mitigated two
ways — the helper degrades to the *old* (correct, merely racy) behaviour if the
key ever disappears, so the failure mode is never a lost write; and
`tests/test_commit_before_response.py` asserts the key still exists, so a
FastAPI upgrade that renames it fails loudly with instructions rather than
silently regressing.

**Testing note worth keeping.** The documented network repro (rapid
create-then-read pairs) did **not** reproduce over loopback even while the
defect was measurably present — server and middleware pacing decide whether a
client can observe it. So the regression tests pin the **ordering** invariant
directly (commit precedes `http.response.start`) rather than racing a live
server. Ordering is the invariant; a lost race is only one symptom of breaking
it, and an in-process ASGI transport can never observe that symptom at all.

The `realdb.client()` harness previously overrode both providers with the old
late-commit bodies, which is exactly why the suite never caught this. The
overrides now mirror the real providers — they exist to swap the *engine*, not
the commit semantics.

---

## 21. CI guards for the three things tests can't fail on

**Decided:** 2026-08-06 · adopted from `project-running`

Three classes of regression are invisible to this repo's test suites, because in
each case the code *works* — it is only wrong later, or wrong for someone else:

- **Bundle weight.** The frontend is adapter-static on GitHub Pages, so every
  kilobyte is paid by every cold visit. No test fails when a dependency doubles
  the payload.
- **Compliance drift.** A migration adds a column holding personal data. The
  DSAR export never learns to include it; erasure never learns to redact it.
  Every test passes. It is wrong on the day someone exercises a data-subject
  right — the worst possible moment to discover it.
- **Env isolation.** This repo is public and deliberately commits
  `*.env.development` so a clone runs with no setup. That is safe exactly as
  long as those files stay boring. gitleaks catches known credential *formats*;
  it does not catch a dev default quietly re-pointed at a production endpoint.

Each now has a workflow. Three choices inside them worth recording:

**Ceilings carry a change log, not just a number.** `web-bundle-budget.yml`
requires a dated entry — measurement plus reason — for every bump. A bare number
tells the next person nothing, so they either raise it reflexively or refuse to
touch it. The log makes "ship the feature, raise the ceiling" a legitimate,
auditable move.

**The compliance detector ships in `warn` mode.** It is heuristic; a heuristic
guard that blocks merges gets switched off, and a switched-off guard protects
nothing. It runs advisory with a documented flip to `fail`. Before landing it I
measured the noise: **0 findings across 726 changed files** of real history,
while still firing correctly on a genuine PII-adding migration. Its own tests
weight the *negative* cases — the ones asserting it stays quiet when the
companion update is present — for the same reason.

**The env guard never prints what it matched.** It reports file, line, and rule
only. A CI log is as public as the repo, so a guard that echoed the credential
it just found would publish the leak it exists to prevent.

**Trade-off:** three more workflows on every PR, and the compliance detector is
pattern-matching that will need its column list extended as the schema grows. A
stale list degrades to silence rather than to false alarms — the failure mode is
"missed a finding", not "blocked a PR", which is the right way round for an
advisory guard.

**Deliberately not adopted:** `project-running`'s `check_production_env` release
guard, which refuses to *build* a release against placeholder endpoints. That
repo ships mobile/watch binaries where a bad endpoint is unrecoverable after
store submission; here the frontend redeploys in minutes and the backend reads
its config at runtime from sops. The guard would cost more than it protects.

## 22. A one-shot money-path sweep owes its failures a row and an exit

**Decided:** 2026-08-15

`services/payment_erp_sync` is the only code path that flips an invoice
`payment_scheduled → paid`. It is dispatched exactly twice, both fire-and-forget
onto a detached thread after a terminal event (run execute, payment webhook),
and **nothing re-invokes it** for a payment that is already `completed` — the
reconciler backstop only re-dispatches payments it moves *out* of
`submitted`/`processing`.

That makes it unlike every other background sweep in this repo. A tick of
`contract_renewal` or `vendor_rescreen` that fails is retried on the next tick;
a leg of this one that fails is permanent. The money has moved, the invoice
never advances, the ERP is never told, and the invoice's aging and 1099 YTD
totals are wrong from then on. Its only trace was a `logger.warning` carrying an
exception class name and a per-run counter that died with the thread.

Three calls behind the fix:

**Reuse `erp_reconciliation`, don't mint a type.** A failed leg means "the ERP
and our ledger disagree and a human must reconcile" — precisely what
`api/erp_webhook` already raises that type for when the ERP reports a void on an
invoice we've advanced past. A new type would have needed a roster entry, a
queue label, a payment-blocking decision, and would have split one situation
across two names in the queue an AP manager works.

**Commit per leg, not per run.** The loop ran every payment in one transaction
with a single commit at the end. A leg failing with a *DB* error poisoned that
transaction, so the final commit raised, the outer handler rolled back, and the
run's successful transitions were discarded too — a strictly worse outcome than
the failure itself, and equally silent. There is no cross-leg invariant to keep
atomic (each payment discharges its own invoice), so per-leg commits cost
nothing and remove the cascade. Each leg re-reads its own rows by id, because
after a rollback the ORM objects the next leg would hold are expired, and
touching one from async SQLAlchemy is a `MissingGreenlet`, not a clean failure.

**The retry endpoint awaits the pass instead of dispatching the thread.** The
two production call sites are fire-and-forget because they run at the tail of a
request that must not block on the ERP. A human clicking "retry" on a strand
wants the *answer*, so `POST /api/payments/runs/{run_id}/sync-erp` awaits
`_sync_payments` and returns its real per-leg counts. Same code path, so the two
can't diverge.

That synchronous retry is also what turned the missing row lock from theoretical
into real, and forced two follow-on details. The pass now takes the invoice
`FOR UPDATE` before the status check, like every other status transition here —
a manual retry can overlap the background thread a webhook just dispatched for
the same run, and two unlocked readers would both see `payment_scheduled` and
both transition, writing a duplicate audit row and a duplicate "invoice paid"
notification (which, unlike the outbound-webhook emit keyed on the invoice id,
has no dedupe). And the result grew a second counter: `synced` counts legs whose
ERP-facing work completed, which stays true for a settled payment whose invoice
is already `paid`, so it can't answer the operator's actual question.
`transitioned` does. Redefining `synced` was rejected because once the real
`adapter.post_payment()` lands, re-pushing an already-`paid` invoice's payment is
still work done.

**Deliberately not done:** auto-resolving the exception when the retry succeeds.
`erp_reconciliation` is shared with the ERP-void path, so closing "the open one"
could silently clear an unrelated reconciliation — the same reasoning
`POST /api/payments/{id}/settlement/accept` records for `fraud_flag`. Also not
done: making `/void` an exit. The money moved; voiding returns the invoice to
`approved`, where it invites a second payment. That the settlement *hold* has
accept-or-void as its two exits and this state has neither is exactly why the
retry endpoint had to exist.

**Trade-off:** a commit per payment instead of one per run — more round-trips on
a background thread, in exchange for never discarding a settled payment's
recorded state. And a repeatedly-failing leg now writes one exception row per
invoice rather than none; the de-dupe on an already-open/escalated row is what
keeps a retry loop from flooding the queue.
