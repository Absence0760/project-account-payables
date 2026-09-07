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

---

## 23. A stale list response is discarded, never merged onto a local edit

**Decided:** 2026-08-15

The list surfaces (`/invoices`, `/vendors`, `/payments`) fetch a page and
replace the whole array. Some of them also edit one row *in place* with no
fetch — the invoice modal's save / file attach, and on `/vendors` a
re-screen, a risk recompute, a block/unblock or an enrichment apply. (Not a
bank-detail save: that one stages a dual-control change request and applies
nothing locally.) Both things can be happening at once: a mount fetch, a
debounced search or a filter-chip fetch is still in flight when the user
approves the invoice they have open.
If that fetch resolves afterwards it holds a snapshot the server took
*before* the edit, and putting it into state silently reverts the edit — the
user watches their approval undo itself. `createRequestSequencer` closed the
fetch-vs-fetch half of this, but a local edit issues no request, so its
counter never moved and the in-flight fetch stayed "latest".

`supersedeInFlight()` closes it: every request issued up to the moment of the
edit becomes un-committable. Two calls worth recording:

**The superseded response is dropped, not merged.** The tempting alternative
is to re-apply the local edit on top of the arriving response — "newest data
plus my change". It isn't sound. The response is a whole-row snapshot from
before the edit, so overlaying the edited fields still publishes stale values
for every *other* field on that row, and for a row the server changed for an
unrelated reason in the same window. Dropping it leaves the list showing
pre-fetch data plus the edit — consistent, if briefly behind — and the next
sequenced fetch (a filter change, the modal close, a reload) reconciles. In
practice every call site mutates from an open modal, so the user cannot have
changed a filter concurrently; nothing they asked for is lost.

**"Can I commit this?" and "am I the newest request?" became two questions.**
They used to be one predicate, `isLatest`. A local edit makes them diverge: the
in-flight fetch is still the newest *request* (nothing newer was issued) but
must no longer commit. The `finally` that clears the `loading` flag has to read
the request question — `isCurrentRequest` — or the spinner stays on forever
after a local edit, with no later request coming to clear it. The vendors
load-error toast reads it for the same reason: a superseded request still
failed, and only a newer *fetch* makes its failure someone else's to report.

**Trade-off:** a fetch issued in the same window as the edit is dropped even
though it might have included the edit, costing one redundant round trip's
worth of freshness. The alternative — assuming it is fresh enough — is exactly
the bug. Conservative is correct here: the cost of dropping a good response is
a slightly stale list; the cost of committing a stale one is losing a
financial edit the user believes they made.

**Not adopted:** tracking per-field pending patches (a mutation overlay, à la
an optimistic-update cache). It solves the merge case properly but needs a
patch log, invalidation rules, and a rollback path on a failed mutation — a
lot of machinery for surfaces whose mutations already happen behind a modal.

---

## 24. A background sweep's failure count becomes state, in one shared runner

All fourteen long-lived sweeps started in `main.lifespan` carried a private copy
of the same loop, and every copy discarded the result its `*_once()` returned.
Twelve of those results already carried a `failures: int`. Nothing read it: its
only consumer was a conditional aggregate `logger.info` inside `*_once` itself.
There was no supervision either — `asyncio.create_task` with no
`add_done_callback` — so a sweep whose loop died was gone for the life of the
process with nothing anywhere saying so, and `GET /api/health` returned a static
`ok` that answered a different question. An `audit_shipping` sink misconfigured
for months looked exactly like one running clean.

Four calls are worth recording.

**The mechanism is a shared loop runner, not a shared reporting call.** The
tempting smaller change is to leave fourteen loops in place and add one
`record(...)` line to each. That preserves the thing the follow-up complained
about: fourteen bodies free to drift. `sweep_health.run_sweep_loop` takes the
tick and owns the whole body, so the outcome is recorded *by construction* — a
sweep cannot forget. The loops keep their own logger and their own
`settings.<interval>` read, so per-sweep log filters and the suites that patch
`<module>.settings` are unaffected; only the body is shared, never the identity.

Making them consistent surfaced a defect the deduplication then fixed once.
`payment_reconciler` carried a comment explaining that `exc_info=True` leaks
`str(exc)` — the stdlib appends the whole traceback regardless of what the
format string names — and had removed it *for itself alone*. Six sibling loops
still passed it and two called `logger.exception`, all of them one adapter error
away from putting a vendor name in the log sink. One body, one posture: class
name only.

**A tick that completes reporting failures is a failed run.** Modelling only
"did the tick raise" would have left the motivating case invisible: the
`audit_shipping` adapters raise *per tenant*, `ship_once` catches that, counts
it, and returns normally. So `failures > 0` (and `vendor_rescreen`'s separate
`vendor_failures`) increments the same consecutive-failure streak a raise does.
The streak, not the absolute count, is the signal — a sweep that fails once and
recovers is noise; one that has failed 37 times running is the months-long
misconfiguration.

**The state is per-process and in-memory, and that is the answer, not a
compromise.** A durable per-sweep run table means an Alembic migration fanned
out to every tenant DB to hold telemetry that is platform-level, not
tenant-level; `Organization.settings` is the only JSON marker available and it
is per-tenant, while these sweeps span all tenants. Both were rejected. The
question an operator actually asks — "is *this* replica's sweep alive and
progressing?" — is exactly what a per-process registry answers, and the
cluster-wide view is the log sink, where every replica emits the same PII-free
`NOT MAKING PROGRESS` line on each streak multiple (a multiple, not every tick,
so a 60-second sweep stuck for a day writes 8 lines rather than 1440).

**`GET /api/health` was left alone, and the new endpoint hides tenant counts.**
Folding sweep health into the liveness probe would turn a misconfigured audit
sink into a rolling restart loop — a degraded background sweep is not a reason
to pull a serving process out of rotation. So the probe stays public and static,
and the operator view is a separate admin-gated `GET /api/health/sweeps`. That
endpoint reports state, timestamps, outcome, streak and the exception CLASS, but
**not** the sweeps' raw counters: an ordinary tenant admin holds `ROLE_ADMIN`,
and `tenants_scanned` would tell them how many organizations the platform
sweeps. Only `last_failure_count` crosses the boundary — the number an operator
acts on, and zero on a healthy platform. The full counters stay in the registry
and the logs, whose reader is already trusted with them.

---

## 25. Sequencing is per list, and a bubble is addressed by identity

Applying §23 across the rest of the app — eighteen list surfaces, plus the two
chat pages — forced three calls §23 didn't have to make, because it only had to
serve three surfaces that each owned exactly one list.

**One sequencer per independent list, not one per file.** Several surfaces hold
more than one list: the `admin` store (users and roles), the `notifications`
store (the list and the 60-second unread-count poll), the `expenses` page (four
tabs), the `discounts` page (offers and the KPI dashboard). A single counter
per surface is the obvious economy and it is wrong: `start()` is monotonic, so
a roles refresh, or a badge poll tick, would mark an unrelated in-flight users
fetch un-committable and blank the list it was about to paint. Each list gets
its own counter. The corollary is that a local edit writing state that *several*
of them load — a mark-read moves `unread`, which both the list load and the
badge poll return — must supersede every one of them; `notifications` has a
`supersedeReads()` for exactly that, rather than one call that looks complete
and isn't.

**`syncUrl()` is untracked wholesale, not just on `search`.** Eight pages'
filter `$effect`s transitively depended on `search` through `buildParams()` and
`syncUrl()`, so a keystroke fired an immediate load alongside the debounced one
(issue #168, fixed on three pages in 2026 and never carried to the rest). The
narrow fix is `untrack(() => search)` at each read. It was taken in
`buildParams()`, whose other reads are filters the caller genuinely depends on
and declares. It was **not** taken in `syncUrl()`, which is untracked in full:
that function is a *writer* of URL state called from the effects, never a
source of dependencies, and its `$page.url` read was already untracked for the
adjacent reason (it writes that URL via `replaceState`, so tracking it
self-triggers the effect). Stating the property once means adding a field to
`syncUrl` later can't quietly reintroduce the bug — and it also removed the
accidental cross-dependencies on `/expenses`, where a tab switch re-fired the
expenses list fetch. The cost is that a genuine dependency can no longer be
declared *inside* `syncUrl`; it has to be read in the effect, which is where a
reader looks for it anyway.

**The chat placeholder is addressed by identity, and `busy` still closes the
window.** `/assistant` let a send start while a saved thread was loading, then
wrote the model's answer into `messages[capturedIndex]` of an array the load
had meanwhile replaced — so the answer didn't vanish, it landed on an
unrelated historical message. Either fix alone would stop today's reproduction:
holding `busy` for the load closes the window, and resolving the placeholder by
a stable client-side id makes a replaced array return `null` instead of a
wrong row. Both shipped, because they fail differently — `busy` is a policy
that any new "replace the messages array" path (a delete, a rename, a
server-push) can forget to respect, while identity is a property of the write
itself. `/cash-flow`'s copilot is a copy of the same code with no
thread-opening rail yet; it got the identity half so it isn't the
index-capturing version someone copies from next.

**A write asks a third question, so the primitive grew one.** §23 split the
old single `isLatest` predicate in two because a local edit makes "may I
commit?" and "am I the newest request?" diverge. A *write* — a save that PUTs
the list and then re-reads it — turned out to need a third: only a local edit
invalidates the payload it just sent, and an unrelated newer *read* says
nothing about that. The first attempt at `InvoiceModal.saveLineItems` read
`canCommit`, which is false for either reason, so an extraction poll's own
reload landing mid-save left the dirty flag stuck on and the Save button up
over a table nobody had touched. `wasSupersededByEdit(token)` isolates the
edit half. It is deliberately a fourth method rather than a second sequencer
or a bespoke boolean in the component: the state it reads (`staleThrough`)
already exists and belongs to the primitive, and a component-local copy would
drift from the `supersedeInFlight()` that sets it.

**Not adopted:** wrapping the three-call protocol (`start` / `canCommit` /
`isCurrentRequest`) in a single `sequenced(fn)` helper. It reads better at
twenty call sites, but it has to decide the `finally` semantics for the caller,
and that is the exact distinction §23 records as easy to get backwards — a
`loading` flag cleared on `canCommit` sticks on forever after a local edit. The
three calls stay visible so the choice stays visible.

---

## 26. Platform extraction falls back to the offline reader locally, never in a deployed env

**Decided:** 2026-08-16 · `backend/app/services/extraction.py::resolve_platform_provider`

`extraction._resolve_extraction_config` hardcoded `provider: "claude_vision"`
for `program_type: "platform"` — the default for every org that sets no
`settings.extraction`, which is every seeded tenant — **regardless of whether
`FEOH_ANTHROPIC_API_KEY` was set**. So on a fresh clone every extraction, an
invoice upload and a PDF supplier statement alike, POSTed to
`api.anthropic.com` with an empty key and came back `provider_error`. The
offline `mock` reader existed precisely so `pnpm dev` could exercise the whole
path with no credential, and nothing routed to it. That broke guard rail 7 for
the one adapter family whose local equivalent was already written.

**The rule now** is the pure `resolve_platform_provider`, in precedence order:
an explicit `FEOH_EXTRACTION_PROVIDER`; else a configured platform key →
`claude_vision`; else, in a non-deployed environment, `mock`; else (keyless and
deployed) `claude_vision` anyway. The committed `backend/.env.development` also
sets `FEOH_EXTRACTION_PROVIDER=mock`, so the choice is visible in the file a
contributor reads rather than only implied by an absent key.

**The last rung is the whole point of the entry.** The obvious symmetric fix —
"no key means mock, everywhere" — is wrong here in a way that is easy to miss:
`MockExtractionAdapter.extract` returns a **fixture** ("Extracted Vendor Inc",
1500.00, a fabricated invoice number), not a read of the document. A deployed
environment that lost its key would therefore stop erroring and start booking
invented payables against real vendors, silently, at 0.95 confidence — inside
the confidence band that can auto-approve. A loud `provider_error` that parks
the invoice in `failed` is strictly better. So the fallback is gated on
`settings.is_deployed`, and a keyless deployed env keeps failing exactly as it
did before this change. `extract_statement` is not symmetric with `extract`
here and that is deliberate: mock's statement reader reads the document's real
text layer and gives up loudly when there isn't one, which is why the PDF
statement path is genuinely exercisable offline while the invoice path is only
*runnable* offline.

**Failing visibly was a requirement, not a nicety.** Both fallback rungs log a
PII-free WARNING naming the provider and the reason; the resolved config
carries `platform_provider_reason`; and the provider already travels on the
persisted result (`InvoiceExtractionResult.method`, a statement run's
`meta.extraction.provider`, surfaced in its provenance panel). A `mock` read is
labelled `mock` everywhere a human looks at it.

**Why the boot-time allowlist.** `get_extraction_adapter` falls back to `mock`
for an unrecognised provider name. Introducing an env var that names a provider
therefore introduced a new way to reach the fixture adapter by typo — in
production. `config.py::_validate_extraction_provider` refuses an unregistered
name at boot, and a drift-guard test cross-checks the literal allowlist against
the live registry, because config.py must not import the service layer.

**Not adopted:** making `mock.extract` return an empty/failed result instead of
a fixture. It would make the fallback safe everywhere, but the fixture is what
lets tests and demos produce a populated invoice with no provider — a much
wider blast radius than the resolution rule this entry is about.

---

## 27. A statement's decimal convention is decided per document, not per token

**Decided:** 2026-08-16 · `backend/app/services/vendor_statement_recon.py`

`850,00` is 850.00 in most of Europe and 85000 if the comma groups thousands.
`parse_amount` assumed the latter unconditionally (`s.replace(",", "")`), so a
European supplier statement reconciled at **a hundred times** its real value.
The text reader agreed the token was money — it read that same comma as
thousands-separator evidence — so nothing upstream caught it.

The unit that can answer is the **document**, not the token. A statement is
written in one convention throughout, so one unambiguous `1.234,56` anywhere in
it settles every bare `1.200` beside it. `detect_amount_convention` runs across
the whole amount column (CSV) or all extracted lines (PDF) before any of it is
parsed.

**Only genuinely ambiguous tokens consult the document.** A single separator
with a three-digit tail (`1,234` / `1.234`) is a thousands group under one
convention and a three-decimal value under the other — that shape, and only that
shape, takes the document's answer. Everything else is self-describing and is
read on its own terms: both separators present means the rightmost is the
decimal point; a repeated separator can only be grouping; and a one- or
two-digit tail must be the decimal point, because money carries at most two
decimal places and no grouping run is shorter than three digits. That last rule
is what makes a lone `850,00` correct with no other row present.

The asymmetry matters: a document-level vote must not override a token that says
what it is, or one malformed row would drag an entire statement onto the wrong
reading. Contradictory evidence therefore resolves to "no answer" rather than to
a majority, and the self-describing tokens still parse correctly underneath it.

**Trade-off:** with no evidence at all, the ambiguous three-digit-tail shape
keeps its historical US reading, so a European statement consisting *only* of
`1.200`-shaped amounts still reads them as 1.200. That is unchanged behaviour
rather than a new failure, and any row carrying cents fixes it for the whole
document.

**Not adopted:** per-token locale guessing (no rule distinguishes `1,234`
American from European in isolation — this is the whole difficulty), and
refusing the ambiguous shape outright (safe, but it makes every European
statement unreadable, trading a wrong number for no number on documents that
are perfectly legible once the convention is known).

**A second, quieter blocker rode along.** `_DATE_TOKEN` matched only `-` and
`/`, so `15.01.2026` read as a second *identifier*-shaped token and the
exactly-one-identifier rule refused the whole row. European statements were
therefore skipped wholesale rather than mis-read — safe, but it meant fixing the
amount alone would not have made one reconcile. Dotted dates are now recognised,
and the amount pattern pins grouping runs to exactly three digits so a date can
never be read as money.

See [vendor-statement-reconciliation.md § Decimal conventions](../backend/docs/vendor-statement-reconciliation.md).

---

## 28. Contrast is guarded per surface in the stylesheets, not per route in a browser

**Decision:** the app's colour palette carries a two-token contract — a base
token for text/icons/borders on a dark surface, and a `-strong` companion that
is the fill behind white text — and a vitest guard
(`frontend/src/lib/a11y/tokenPairing.test.ts`) scans every stylesheet in `src/`
for a pair that breaks it. The route-level axe guard
(`tests-e2e/a11y/axe.spec.ts`) stays, and keeps the half the scanner can't do.

**Why.** The axe guard only sees what a listed route happens to render. Twice
running, that shape produced the same outcome: a WCAG 1.4.3 failure was caught
on the pages inside the route list and missed, identically, on the pages
outside it. `--text-muted` on `--surface-2` was 4.34:1 *wherever it appeared*;
axe found it on `/admin/api-keys` and `/admin/webhooks`, and `/billing`'s
proration box — the same defect — was found by a human reading the file. The
bug recurs per **surface**, so the surfaces are what to check.

Running the scan the first time found 99 problems, which is the real argument
(a fourth rule, added once the first three were green, found 106 more — see
below):

- **55 contrast failures**, almost all one root cause. `--accent-strong` had
  been added specifically so white text had somewhere legible to sit, and
  almost nothing used it — 40 buttons, chips and chat bubbles still filled
  with `var(--accent)` at 3.12:1. Green (`#1fa86a`, 3.06:1) and red
  (`#e04040`, 4.22:1) had no companion at all, so pay / approve / execute /
  reject / void — the money buttons — all failed.
- **32 `var(--token, fallback)` declarations whose fallback contradicted the
  token.** Inert while the token exists, and the wrong colour the moment one is
  renamed. This is exactly how `--surface-2` shipped for months rendering a
  value nobody had declared, with two call sites disagreeing about it.
- **12 references to a token nothing ever assigns**, where the fallback is
  always what renders — including two spellings of the monospace stack.

**Scope, deliberately.** The scanner checks pairs decided **inside one rule**.
A rule that sets only `color` inherits its background through the cascade,
which is a runtime question; that stays axe's job. Neither guard subsumes the
other, and saying so here is cheaper than someone deleting one of them later.

**One sound question survives in the cascade case**, though, and asking it
found the round's largest single defect. A rule setting only a `color` will
render on *some* app surface, so a **literal** there has to be legible on the
surfaces body text actually sits on. `#e04040` — the status red — is 4.11:1 on
`--surface` and 4.47:1 on `--bg`: failing, in **106** declarations across 61
files, on error messages, alerts and the danger row-action. Only literals are
asked (a palette token is already asserted against those surfaces), `#fff` and
`#000` are exempt as the deliberate on-a-fill choices, and `--surface-2` is
*not* in the surface list — text on that raised panel declares its background,
so the pair check owns it, and including it would flag every status colour in
the app on the strength of a surface it never renders against. Decorative
fills (a chart bar, a confidence dot, an SVG `fill`) carry no text and are
untouched.

The rule also fires when the rule *does* declare a background that resolves to
nothing usable — a translucent tint, a gradient. Standing down there sounded
conservative and was the opposite: `background: rgba(140,100,240,0.15);
color: #8c64f0` is the standard dark-theme status pill, so the check would have
fallen silent precisely on the pills. A tint over an app surface composites
close to it, so the bare surface is the right approximation; twelve more
failures came out (purple, blue, amber and green pill/banner text, 3.53–4.42:1).

**A hole in the drift check itself** turned up in review and is worth
recording: the `var()` fallback was captured with `[^()]*`, which cannot cross
an inner paren, so `var(--bg, var(--surface))` — shipped on two routes —
matched nothing and was invisible to both the dead-token and the stale-fallback
check. The scan is paren-aware now, and the comparison resolves a fallback
through the palette rather than by spelling, since a token-valued fallback
would otherwise read as stale on sight.

**Fixing a failure means changing the colour.** There is no suppression
mechanism and no allowlist, because the `-strong` companions mean a correct
answer always exists. The one nuance encoded instead of waived is WCAG's own:
a rule that declares a large-text size in the same block is held to 3:1, and an
unresolvable size (`em`, `calc`, inherited) is treated as normal text — the
stricter direction.

**The palette's own contract is asserted directly**, not inferred from the
rules that happen to use it. A token drifting light would otherwise surface as
dozens of scattered failures instead of one.

**Not adopted:** widening the axe route list alone (the ask that prompted this
— it treats the symptom, and the list will trail the app again the next time a
route is added); a linter plugin (a colour comparison needs the resolved token
values, which is the part a generic CSS lint doesn't have); and blocking a
tenant's brand colour. On that last one — `brandThemeVars` writes a tenant's
`accent_strong_color` straight into `--accent-strong`, so a brand colour can
defeat the whole contract at runtime where no static scan can see it. The
backend accepts any valid hex and the brand is the tenant's call, so the
answer is an advisory: `accentStrongContrast` reuses the same WCAG primitive
and both surfaces that edit that colour — the org Branding panel and the
partner child-branding modal — show the real ratio before it is saved.

**The scan also models a rule's own `opacity`, and deliberately stops there.**
Adding `/cfo` to the axe list caught `.kpi-sub` — `--text-muted` under
`opacity: .85` — at 4.24:1, which *both* static checks had structurally missed:
the same-rule pair check compares the colours as declared, and the bare-literal
check exempts a palette token on the reasoning that this contract already
vouches for it. Opacity is exactly what invalidates that reasoning, because it
composites text and its background together down onto the backdrop. So a rule
that fades itself is now measured as it renders, and the eleven instances that
found were all the same shape: an `opacity` line fading text a token had
*already* muted. Every fix was deleting the line — the fade was never carrying
meaning, only cost.

An **ancestor's** opacity stays out of scope, and that is the boundary, not an
omission: resolving it means resolving the cascade, which is the half axe owns.
`.status-pill.revoked` on `/admin/api-keys` is the worked example — a row fade
dragged an already-muted pill to 2.44:1, and only a browser could see it. The
fix spares the status cell from the fade, because the one cell explaining why a
row is faded should not be the least readable thing in it.

Translucent *backgrounds* are the same compositing problem and are measurable
with the same primitives, but 29 badges sit 4.15–4.48:1 — failing by a hair,
needing a design call rather than a mechanical edit. Arming that half before
fixing them would ship a red build, so it is tracked with its measurements in
[followups.md](followups.md) instead.

See `frontend/CLAUDE.md` § Colour tokens and contrast, and
[accessibility.md](accessibility.md).

---

## 29. A mis-typed provider name never resolves to the fixture adapter

**Decided:** 2026-08-16 · `backend/app/services/{payment_adapters,erp_adapters,fx_adapters}/dispatcher.py`

All three dispatchers resolved an unrecognised provider name to their `mock`
adapter. Each had written down why — "prevents a missed config from silently
500-ing the entire payments domain", "so a typo'd config doesn't blow up
sync-erp", "fails closed in prod because the mock returns a fixed rate that
will not match real market" — and each was reasoning about a `mock` that does
not exist. These fixture adapters are not inert stubs; they are the thing that
makes `pnpm dev` work with no cloud account, so they answer **yes** to
everything:

| Family | What `mock` does | What a typo'd `settings.*` produced |
|---|---|---|
| payments | `create_payment` → `success=True, completed` | every payment in every run reported as settled, invoices flipped to `paid`, no money moved |
| payments | `parse_webhook` verifies no signature | the public webhook route reached an unverified parser, under a name the `provider == "mock"` early-return cannot catch |
| payments | `void_payment` → `True` unconditionally | a `voided_upstream` audit row for a rail nobody asked |
| ERP | `post_invoice` → `success=True` + a `MOCK-…` id | the invoice walked `sending_to_erp → sent_to_erp → done` with an ERP reference pointing at nothing |
| ERP | `test_connection` → `True` | `POST /organization/test-erp` answered "Connected to `<typo>` successfully" — the endpoint that exists to catch the misconfiguration confirmed it |
| FX | `get_rate` → a hardcoded table | `prepare_international_payment` **locked** the fabricated rate onto `Payment.fx_rate` / `source_amount`, never re-fetched, driving the real outflow and later `realized_fx_gain_loss_for_settlement` |

**The rule now:** no configured provider still means `mock` — that is the
local-first default (guard rail 7) and an org that has configured nothing is a
normal state. A **named** provider we have no adapter for raises
(`UnknownPaymentProviderError` / `UnknownErpAdapterError` /
`UnknownFxProviderError`).

**This is §26's call, one layer down.** There the same fallback let an
unrecognised `FEOH_EXTRACTION_PROVIDER` reach a fixture adapter, and the fix
was a boot-time allowlist. That doesn't transfer: these names come from
per-org `Organization.settings`, not process env, so there is no boot at which
to check them. The refusal has to live at the dispatcher, and — because a
dispatcher cannot know whether its caller is moving money or drawing a chart —
**each caller decides what the refusal means**:

- **Refuse, before any state changes.** Run execute / resume / retry-failed and
  the compliance release resolve through `_require_payment_adapter` *before*
  claiming the run, so the answer is a 409 with the run still `draft` rather
  than a 500 with it stranded `executing`. The three ERP sync endpoints 400 —
  a config problem, not the 502 they use for a gateway failure.
- **Fail the one payment.** The international leg records
  `failure_reason="fx_provider_unsupported"` instead of booking a rate. The
  reason names the condition and NOT the admin's raw settings value, because
  every AP user reads `failure_reason` while only an admin owns the setting.
- **Degrade.** `fetch_provider_balance` falls back to the manual opening
  balance; the CFO dashboard's unrealized-FX panel reports `available: false`;
  the corridor auction skips just that provider so one bad name in a
  multi-provider list can't take the whole auction down.
- **Record and continue.** `/void` still voids locally — the books should
  reflect intent — but writes `provider_not_supported` rather than a
  fictitious `voided_upstream`.
- **Count it as a failure.** The payment reconciler lets it propagate so the
  tenant registers as a sweep failure and shows `degraded` on
  `GET /api/health/sweeps` (§24).
- **Fail the leg, not the pass.** `payment_erp_sync` resolves the ERP adapter
  *inside* `_sync_one_leg`, where it would be used, so an unsupported type
  travels the same path as any other leg failure and opens the de-duped
  `erp_reconciliation` exception §22 introduced. The first attempt at this put
  the check in `_sync_payments` as a pre-flight, before the tenant session
  exists — which cannot open an exception, aborts every payment in the run at
  once, and returns a count that `_run_in_thread` discards on the primary
  dispatch path. That reintroduced exactly the invisible strand §22 removed:
  money moved, invoices frozen at `payment_scheduled`, nothing in the queue.
  A config error must strand the same way a transport error does.
- **Say which name is wrong.** `POST /organization/test-payments` and
  `/test-erp` echo the bad value and list the registered alternatives. The name
  is bounded to 50 chars (its column width) so an absurd value can't bloat a
  log line, and no credential from the posted config is echoed.

**Why not make the mock adapters inert instead.** §26 rejected the same idea
for extraction and the reason holds here: the fixtures are what let tests and
demos exercise a whole money path with no processor. Neutering them has a far
wider blast radius than the resolution rule this entry is about.

**Not adopted:** validating `settings.payments.provider` on write in
`PATCH /api/organization`. Worth doing, but it is not sufficient on its own —
settings predate any validator, arrive from seeds and migrations, and an
adapter can be *removed* from the registry after a name was already stored.
The dispatcher is the only chokepoint every caller passes through.

See `backend/docs/payments.md` § Provider resolution.

## 30. A translucent tint is composited, not approximated — and each tone names its own text

§28 armed a bare-literal `color:` check against the surfaces body text sits on,
and deliberately let it also fire when a rule *did* declare a background that
resolved to nothing usable — a gradient, or a translucent tint. Its stated
reasoning for the tint half: "a tint over an app surface composites close to
it, so the bare surface is the right approximation."

**That approximation was wrong in the unsafe direction, and this entry
supersedes it.** A tint does not composite *close to* the surface; it moves the
surface *toward the colour of the tint*, which in the status-badge recipe is
the same hue as the text on top of it. Every such badge therefore renders with
less contrast than the bare surface predicts, so the check passed them. 29 did,
between 4.15:1 and 4.48:1 — failing by a hair, which is why none was caught by
eye either. Accent text on its own 15% tint measures 5.55:1 against the bare
surface and renders at 4.48:1.

**Composite it instead.** The primitives were already there and unused on this
path (`parseColorWithAlpha`, `compositeOver` — §28 added them for the `opacity`
case). A translucent background now resolves *with* its alpha, is composited
over each backdrop, and the rule's own opacity applies on top. Both compositing
causes became one calculation rather than two checks that could disagree.

**The fix is a third token role, not 29 new hexes.** The palette already paired
a base token (text on a dark surface) with a `-strong` companion (the fill
behind white text). Badges get `--<tone>-tint` and the `--<tone>-on-tint` text
calibrated on it, for five tones. The values are not new: `StatusBadge` had
solved this for its own six tones by lifting the text rather than darkening the
tint, and those hexes are what the tokens promote out of one component's
private stylesheet.

- **Why not per-hue alphas.** Cheapest and most fragile. Accent passed only at
  alpha ≤ .14 and sat at .15 — one hundredth — so every future nudge silently
  re-breaks it, and nothing states the rule.
- **Why not opaque tint tokens.** It would move these rules under the existing
  same-rule pair check and need no compositing at all. Rejected because an
  opaque tint bakes in ONE backdrop, and badges sit on both `--bg` and
  `--surface`; and because the guard has to composite anyway to catch the next
  hand-rolled `rgba()`, so the simpler check would have bought nothing.
- **`--danger-on-tint` equals `--danger`.** Red is bright enough to clear its
  own tint unaided. The token is declared anyway so the rule stays "tint
  background, matching `-on-tint` text" with no exception to memorise, and so
  red can be recalibrated later without hunting the sites that spelled it the
  other way.

**The guard ships after the sites, not with them.** An armed guard with 29
known failures is a red build, not a guard — so the tokens landed, then the
badges moved onto them, then the compositing was switched on.

**What this did not fix.** ~208 rules still spell a tinted badge as a
hand-rolled `rgba()` plus a literal hex — about 40 spellings of the five tones
now named. All of them pass, so that is design-system debt rather than a
defect, and normalising it shifts tint strength across the app. Tracked in
[followups.md](followups.md).

**A regression the move caused, recorded because the shape recurs.** Folding
two hand-picked oranges onto one warning tone made `/payments`'
`pending_compliance` identical to `pending` — the invisibility that status was
made first-class to end. Sharing the tone is right (both are waiting); the
distinction moved to a ring. When a normalisation collapses two colours, check
what those colours were silently carrying.

See `frontend/CLAUDE.md` § Colour tokens and contrast.

---

## 31. A canonical helper with no caller is a bug that has usually already happened

Eight built, tested, documented capabilities had no production caller
([followups.md](followups.md) § Backend capabilities with no production caller).
The tempting read is that they are spare capacity awaiting a consumer. Closing
all eight in one round showed the opposite: in three cases the reason nothing
called the helper is that somebody had already re-implemented it inline, and the
copies had drifted into live defects.

- `analytics.compute_dpo_trend` was re-derived twice in `api/analytics.py`, and
  the copies disagreed about whether `rejected` invoices belong in the COGS
  proxy — so `/api/analytics/drill/dpo` reported **3.0 days where the chart it
  exists to explain showed 30.0**. Same failure shape as issue #126.
- `workflow_engine.is_known_step_type` was written to be the shared gate and
  never wired, so `POST /api/workflows/import` — the one save path a Pydantic
  `Literal` does not constrain — persisted a typo'd `"aproval"` that the engine
  then silently skipped. An unrecognised step type is not a degraded step; it is
  an *absent* one, and the absent one is usually the approval.
- `international_payments.is_international_payment` was dead while three modules
  hand-rolled its rail set. Unifying them surfaced that
  `compliance._kyc_required_for` compared a raw per-org override against a
  lower-case `Payment.method`, so an admin entry of `"SEPA"` disabled the KYC
  gate for that corridor — and a blank `[""]` produced a truthy set that
  disabled it for **every** corridor. A fail-open on a regulatory control,
  invisible because the code path that would have prevented it was the unused
  one.

The rule this yields: when a helper exists and nothing calls it, look for the
inline copy before assuming the feature was never finished — and treat the
divergence between them as the actual finding.

## 32. A step type we don't recognise is refused by name, never silently ignored

`validate_builder_steps` previously passed over any type outside the five
builder types, reasoning that canonical steps were not its concern. But the
engine reads `steps_config` by type *name*, so an unrecognised type is absent
rather than degraded — a typo reads as "no approval step configured" and the
workflow quietly loses a financial control. Failing the import loudly is
strictly safer than persisting a config whose runtime meaning is "one fewer
gate". Same posture as §29.

Two follow-on calls, both deliberate:

**`create_workflow_step` refuses a builder step type rather than inventing a
number for it.** `WorkflowStep.step_number` is the canonical pipeline's 1-based
index, and `complete_current_step` finds the open step by ordering on it. A
builder step is orchestration config with no place in that ordering, so
`canonical_step_index` raises `NonCanonicalStepTypeError` instead of allocating
a number that would corrupt the queue. Both new errors subclass `ValueError`, so
any handler written against the old bare `.index()` still catches.

**`/drill/dpo` serializes money as exact decimal strings while the rest of
`api/analytics.py` stays float.** That inconsistency is chosen, not overlooked:
the module has ~57 more float money fields feeding shipped frontend and mobile
consumers (`analytics.ts` types them `number`; `CfoMetrics.svelte` calls
`.toFixed()`), so converting them is a separate client-breaking migration,
tracked in followups. `/drill/dpo` has no shipped consumer, so it could be
corrected in isolation and was. `dpo` itself stays a JSON number — it is a day
count, not money. Rejected: leaving `/drill/dpo` on float for consistency with
its neighbours; consistency with a violated invariant is not a reason to keep
violating it.

## 33. A card that cannot be handed a signature signs itself

The Teams approval card's buttons are MessageCard `HttpPOST` actions dispatched
by Microsoft, not by us — which is the whole problem. A Slack interactivity POST
arrives signed by Slack's infrastructure and a Teams *Outgoing Webhook* POST
arrives signed with the shared security token, but an actionable-message action
arrives with whatever we put in its `headers`. There is no handshake to ride on,
which is why the inbound half shipped, was security-reviewed, and then sat
unreachable.

Three options. Point the buttons at the existing email-approval confirm page
(`OpenUri` + an `email`-channel token): safe, but it routes around the shipped
Teams endpoint entirely and breaks the channel binding that keeps each surface's
tokens non-interchangeable. Register a Bot Framework app and use
`Action.Execute`: a different auth model (Bot Framework JWT validation), not an
increment on the shared-secret gate already reviewed. Or sign the action
ourselves.

We sign it ourselves. We control the action's exact `body` string, so the card is
stamped at render time with `HMAC-SHA256(security token, body)` and the endpoint
re-derives it over the raw bytes. What that digest is matters less than what it
is **not**: it is not a key, and it is body-bound, so an approver handed the
Reject action cannot upgrade it to Approve, and publishing one valid
`(body, digest)` pair yields nothing about the token. What it proves is that the
POST replays a body the platform minted — which is exactly the job of keeping
blind probes off a public endpoint. Anyone who can read the digest already holds
the action token sitting in plaintext in the same JSON, and re-firing it is
collapsed to a no-op by the single-use `jti`. So the exposure is unchanged from
the accepted Slack precedent: any channel member can click.

Two consequences that look like oversights and are not:

- **The digest rides two headers.** Teams may replace an actionable message's
  `Authorization` with its own bearer token, stripping our only proof, so the
  card also carries the digest on `X-Feoh-Card-Signature`. The endpoint collects
  every candidate and accepts if any verifies — not weaker (each must reproduce
  the HMAC of the exact body) but robust to a proxy folding duplicate
  `Authorization` values into one string, which would otherwise mask a good card
  signature behind a mangled one.
- **The card emits no timestamp header.** It is stamped at *render* time, so the
  ±5-minute replay window would kill the buttons five minutes after the card was
  posted. Replay stays bounded by the single-use `jti` and the workflow state
  machine — the posture the endpoint already documents for a timestamp-less POST.

Rejected alongside: minting the token on the `email` channel so the card could
reuse the confirm page. That would make a Teams card's credential redeemable at
the email endpoint, dissolving the channel claim that exists precisely to keep
one surface's token off another.

## 34. An attestation nobody made reports "unknown", not the default

Two verdicts landed this round that could have defaulted to the reassuring
answer, and both deliberately do not.

**Data residency.** `check_residency_alignment` compares a tenant's pinned region
against `FEOH_DEPLOYED_REGION`. The obvious shape — default the deployed region
to `DEFAULT_REGION` and return a plain `bool` — was rejected: it makes the
*absence* of a declaration indistinguishable from a verified match, and hands an
EU-pinned tenant `aligned: true` on the strength of nobody having said
otherwise. That is the precise failure a residency control exists to prevent,
and it fails in the reassuring direction, which is the one nobody investigates.
So the verdict is tri-state (`aligned` / `misaligned` / `unknown`) with
`aligned: bool | None`, and `None` only ever pairs with `unknown`; a `reason`
(`deployed_region_unset` / `deployed_region_unrecognised`) makes the unknown
actionable rather than merely honest. An *unrecognised* token reports `unknown`
rather than comparing literally, because one typo (`eu-central-1` for `eu`)
would otherwise mark every tenant misaligned and bury the real ones. Boot-time
validation was also rejected: `config.py` refuses to start on a bad
`FEOH_EXTRACTION_PROVIDER` because that value silently turns a deployed pipeline
into a fixture generator, but this one changes nothing except a report, so
refusing to start over it trades a wrong answer for an outage. Advisory
end-to-end — nothing routes, blocks, or moves data on the verdict.

**Adverse media.** The sanctions taxonomy (`sanctions` / `pep` /
`adverse_media` / `high_risk_country`) shipped on `ScreeningResult.categories`
and was dropped by all three consumers, so negative-news coverage that has not
yet reached a formal list reached nothing that acts. It now rides the row's
existing JSONB under a reserved key — not a new column: the payload is a small
fixed enum list and the only read pattern is "latest row per vendor", so a
column would fan a schema change out to every tenant DB to store it. The merge
never mutates the provider's own payload (a `clear` screen's payload stays
byte-identical, so an auditor replays exactly what was returned) and reads are
tolerant, because a screening-trail row must never 500 the risk endpoint. The
labels are *our* fixed vocabulary rather than provider free text, which is what
makes them safe on an audit row, in an API response and in a UI badge —
`raw_response` itself is still never serialized out (invariant #7). And **a
`clear` verdict carrying adverse media becomes a `hold`**: no shipped adapter
produces that combination today, so it changes no live flow, but auto-allowing
it is exactly the gap the taxonomy was added to close.

**Mileage, by contrast, is advisory — and that is the same principle, not an
exception to it.** `expense_policy.BLOCKING_CODES` holds exactly two codes, both
*missing evidence the submitter can supply*, so the 422 is actionable: attach
the thing and resubmit. A `mileage_amount_mismatch` is a disagreement between
two numbers already supplied, and which one is wrong is a human judgement (the
rate changed mid-period, the line bundles a toll, the claim is deliberately
*under* entitlement). Blocking would strand a legitimate claim with no in-app
override and would refuse a submission for an under-claim that costs nothing.
The approver is the control point, and the violation carries the expected figure
plus its working so the badge says what to pay. Fail *informative*, not falsely
reassuring — and not falsely obstructive either.

## 35. A figure we cannot establish is excluded and counted, never estimated at face value

Rounds 10 and 11 found the same defect five times, in five unrelated modules,
and fixed it the same way each time. Recording it once so the sixth is caught in
review rather than in production.

The shape is always this: `Payment.amount` is denominated in the **invoice's**
currency — `international_payments.prepare_international_payment` sets
`amount=invoice.amount` and puts the home-currency debit on
`source_amount`/`source_currency`. Any aggregate that sums raw `Payment.amount`
across a book containing one foreign invoice is therefore a silent two-currency
mixture, and every one of these labelled that mixture with the org's reporting
currency. The 1099 report summed it onto a **filed tax form** (a EUR 1 000
invoice paid on a USD 1 100 wire reported `ytd_paid: "1000.00", currency:
"USD"`, which can also push a vendor across the $600 threshold); vendor risk
scoring fed it to a ramp whose full-exposure point is a bare `100000`, pinning a
¥10 000 000 payer at the maximum sub-score; the discount optimizer added a EUR
1 000 offer to a USD 1 000 one and checked the sum against a single-currency
cash budget; the cash-position curve produced a **−9 751 000** closing balance;
the reporting-currency lock kept a stale figure and reported it as *converted*.

**The rejected fix in every case was a face-value fallback** — treat 1 000 EUR
as 1 000 USD when no rate is available, optionally flagging it. That is what
`reporting_amount_for_row` does for a spend dashboard, and there it is right: an
approximate total with a caveat beats a blank panel. It is wrong the moment the
number is *filed, gates a control, or is compared against a threshold*, because
the caveat rides a different field from the number and only the number gets
read. One of these had already shipped the caveat — `_commitment_rows` computed
an `unconverted` flag whose docstring promised it made foreign rows "visible
rather than silent", and grep found **zero readers**. A flag nobody reads is not
a mitigation.

**Fetching a rate at read time was rejected too.** It makes a historical total
move under the reader, which §18 already settled for locked-not-recomputed
rates, and it puts an FX outage on the path of a tax report.

So `currency_conversion.payment_reporting_amount_sql` resolves two rungs, most
authoritative first — the rate-locked `source_amount` when its currency IS the
reporting currency, else `amount` when the invoice's own currency is. There is
deliberately no third rung. What neither rung establishes is **excluded from the
total and counted separately** (`unconverted_payment_count`,
`unconverted_payments`, `unconverted_count`, `unconvertible`), the count is
surfaced on the API, the `/cfo` card and the copilot tools, and an affected
vendor is flagged `needs_attention`. A single-currency tenant only ever reaches
rung 2, so its numbers are byte-identical — which is why this was invisible for
so long.

The general rule, beyond currency: **when a value cannot be established, say so
in the result and leave it out of the arithmetic.** Do not substitute a
plausible stand-in and record the doubt somewhere the consumer does not look.
Same instinct as §34 — the reassuring default is the one nobody investigates.

---

## 36. An unresolvable sanctions provider holds the payment; it does not screen against `mock`

**Decided:** 2026-08-19 · `backend/app/services/sanctions_adapters/dispatcher.py`

`get_sanctions_adapter` resolved `_REGISTRY.get(provider) or _REGISTRY["mock"]`,
so a typo'd `Organization.settings.compliance.sanctions.provider` —
`"worldcheck"` for the registry's `refinitiv`, say — screened an entire tenant's
vendor book against the mock's three-entry fixture list and returned `clear`
with risk 0. The dispatcher's own docstring asserted that "the compliance
service surfaces a warning in its result so this misconfiguration is visible to
the AP team". No such code existed: `services/compliance` never inspected
`adapter.provider_name`, and `services/vendor_screening` recorded the mock's
verdict unexamined. The stated mitigation was never built, on the control that
exists to keep money away from a sanctioned party.

A **named** unknown provider now raises `UnknownSanctionsProviderError`. An
absent or empty provider still resolves to `mock` — that is the local-first
default, and a fresh clone must screen vendors with no cloud account. This is
the same call already made for `erp_adapters`, `payment_adapters`, `fx_adapters`
and `financing_adapters` (§29): the mock is never an inert stub, so substituting
it converts a configuration error into a silent, confident wrong answer.

Rejected: raising all the way out to the caller. A 500 on the vendor-create path
would break vendor management over a compliance misconfiguration, and a 500 on
the payment path tells an operator nothing about which control failed. Both
consumers absorb the raise instead, each in the direction that fails closed.
`compliance.check_payment_compliance` returns `hold` with the reason `sanctions
screening could not run: no adapter for configured provider '<name>'`, so the
payment waits in `pending_compliance` and the caller opens the usual
`payment_compliance_hold` exception — the misconfiguration reaches the AP queue
rather than the payment rail. `vendor_screening.screen_vendor_record` writes a
`sanctions_checks` row with `provider="unconfigured"`, `result="review_required"`
(the requested name rides `raw_response`, PII-free) and denormalises
`vendors.screening_status="review"`, so the vendor lands on the review queue
instead of reading `clear`. No payment block is set — a misconfiguration is not
a match.

## 37. A webhook's uniform ack covers decisions, not our own failures

**Decided:** 2026-08-19 · `backend/app/api/email_intake.py`, `backend/app/api/erp_webhook.py`

Every inbound webhook returns the same opaque response on every rejection path,
so the response cannot enumerate tenant slugs or bearer tokens. For
`email_intake` that posture is unusually load-bearing: the HMAC signing secret is
platform-wide (the email provider has no notion of tenants), so anyone who can
sign a request could otherwise grind candidate per-tenant intake tokens and watch
for the response to change. The uniform `200 {"status": "received"}` is what
closes that.

It was applied too widely. `services/email_intake.process_inbound_email` releases
its Redis dedup claim and re-raises on any downstream failure — S3 unreachable,
tenant DB down, Redis flapping — commenting that this "lets the NEXT delivery of
the same message_id actually retry the work". The route then caught that re-raise
and acked `200`, which tells SES / Mailgun the message *was* delivered. There was
no next delivery. The release-on-failure code was preparing for a retry that
could never arrive, and the vendor's invoice was gone behind a log line.
`api/erp_webhook.py` had the identical shape for ERP status transitions;
`api/billing_webhook.py` faced the same choice and had already gone the other way.

The distinction drawn is **decision vs. our own failure**. A decision is a final
answer about this message — unknown or disabled intake token, duplicate delivery,
no usable attachment, unknown tenant / status / invoice, a transition the state
machine forbids, genuine success. Those keep the uniform ack, because varying
them is exactly the oracle. A failure of ours is not an answer at all: the
message is still unprocessed work, and the only correct response is to ask for it
again. Those now return a **bodyless 5xx** (`_retry_please()` → `503`) in both
modules.

The residual exposure is accepted deliberately. A 5xx on our own failure narrows
the token-enumeration oracle to "while the platform is already broken" — an
attacker learns only that something inside us threw, never whose token they
guessed, and only during an outage. Losing every invoice that arrives during a
blip is the larger harm by a wide margin. The response is bodyless, so it still
carries no detail, no stack trace and no tenant.

Rejected: making every path a 5xx (an ERP would then retry forever on an event we
have correctly and permanently refused — the mirror-image failure); and leaving
it alone with a louder log (the log was already there, and it had not made anyone
whole).

## 38. Scheduled reports ship, with a CRUD surface, tenant- but not entity-scoped

**Decided:** 2026-08-19 · `backend/app/api/scheduled_reports.py`

`services/scheduled_reports.py` had a complete, tested runner and no input
surface: nothing under `app/api/` referenced the `ScheduledReport` model, so a
row could only be created by hand-written SQL, `list_due_schedules` returned `[]`
on every tick forever, and the documented 5-strike auto-disable was a one-way
door. The choice was ship the surface or delete the feature. We shipped it —
recurring emailed CFO reports are a table-stakes AP capability and the machinery
was already sound.

Three calls inside that:

*Admin-only to mutate, admin + CFO to read.* A schedule is a standing instruction
to email a CSV of the tenant's AP spend to an arbitrary address on a recurring
basis, with no review of any individual send. That is a data-egress control, not
a reporting preference, so it sits above the gate the rest of `/analytics` uses.
Reads stay open to the CFO, who owns the reports and needs to audit what is going
out.

*Validation against the runner's own registries, never restated copies.*
`report_type` is checked against `report_export.EXPORTERS` and `cadence` against
`scheduled_reports.known_cadences()`. Both matter operationally: a `report_type`
outside the exporter registry raises on every tick and burns through the
auto-disable without ever sending, and an unknown cadence silently falls back to
daily — so a "yearly" row would have emailed 365 times a year. Importing the
registries means adding a report type or a cadence updates the API for free.

*Tenant-scoped, deliberately not entity-scoped.* `ScheduledReport` carries no
`entity_id`, and the runner's `_materialise_rows` applies no entity filter to any
of its six report types — the emailed CSV is whole-tenant by construction.
Stamping an entity on the schedule row would advertise a scope the delivered file
does not honour, which is worse than not offering it at all. Making it real means
entity-filtering the materializer *and* a migration; that is its own slice.
Tenant isolation itself is enforced normally, through `get_tenant_db`.

Audit rows carry the recipient **count**, never the addresses: the trail is
append-only and WORM-shipped, so a corrected distribution list could never be
redacted out of it. And `PATCH {enabled: true}` clears the stale `[retry N]`
marker — `_mark_failure` reads that prefix to count consecutive failures, so
re-enabling without clearing it means the next failure lands at retry 6 and
disables the row again immediately, which an operator cannot distinguish from the
re-enable not having worked.

## 39. `/api/v1/docs` is a self-hosted reference, not Swagger UI

**Decided:** 2026-08-19 · `backend/app/api/v1_openapi.py`

`public_docs` returned FastAPI's `get_swagger_ui_html`, whose only stylesheet,
script and favicon are third-party CDN URLs. `main.SecurityHeadersMiddleware`
stamps `Content-Security-Policy: default-src 'none'` on every response, so the
route returned `200`, fetched nothing, and rendered blank in any browser honouring
the header.

Three exits were available. **Allowlisting the CDN** in a route-scoped CSP is
three lines, but it gives a page the platform itself serves a third-party runtime
dependency, and it still renders blank offline — breaking guard rail 7's
local-first rule. **Dropping the route** and pointing at the spec URL is honest,
but leaves a 404 on a URL our own docs advertise. **Vendoring `swagger-ui-dist`**
keeps the CSP strict and works offline, but commits roughly a megabyte of
third-party JavaScript to a public repo along with a version nobody will remember
to bump — a supply-chain artifact acquired for a reference page.

We took the third exit at its minimum. `render_docs_html` renders the same
published document server-side as self-contained HTML: no script at all, inline
or sourced, and no external asset of any kind. The route sets its own CSP —
`default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri
'none'` — which differs from the global policy by exactly one token, the page's
own inline stylesheet, which can neither execute nor exfiltrate. The **global**
policy is untouched: it is what keeps the API origin unable to load third-party
script at all, and relaxing it for one page relaxes it for every JSON response.
`SecurityHeadersMiddleware` uses `setdefault`, so a route-set header wins with no
middleware change.

The trade-off, stated plainly: this is a reference, not an interactive console —
there is no "Try it out". The machine-readable contract at
`/api/v1/openapi.json` is what integrators actually consume, and it feeds any
client generator or Swagger / Redoc instance they already run. `render_docs_html`
is a pure function of the spec, so the page cannot drift from the routes.

## 40. Semantic duplicate detection stays cross-entity; the warning it writes does not

**Decided:** 2026-08-19 · `backend/app/services/duplicate_detection.py`

`find_semantic_duplicates` queried `invoice_embeddings` with no entity filter
while every sibling reader is scoped — `rag.retrieve_similar` takes an
`entity_id`, the assistant's `find_invoices_by_text` threads it "so 'search'
can't silently widen past the subsidiary the user has selected", and
`vendor_matching._candidate_query` scopes for the same reason. Two consequences
followed: the resulting `duplicate` Exception is payment-blocking, so a
cross-entity false positive holds subsidiary A's payment run; and
`matches_to_warning` wrote the matched invoice's `invoice_number`, `vendor_name`
and `amount` into `invoice.warnings`, which the detail modal renders and whose
`message` is copied verbatim into the Exception description — data from an entity
the viewer is otherwise scoped away from.

Scoping the search would have been the mechanical fix and it is the wrong one.
The same invoice billed to two subsidiaries of one group is a *real* duplicate
and precisely what a group AP team wants caught; silently scoping removes a
genuine control in order to fix a disclosure problem. So the search stays
tenant-wide and `entity_id` **classifies rather than filters**: an outer join to
`invoices` tags each match `cross_entity`, and a cross-entity match is reported as
existence only — "a near-identical invoice exists under another entity", with
`invoice_id`, `invoice_number`, `vendor_name` and `amount` all nulled. The id goes
too: an entity-scoped `GET` would 404 on it anyway, so surfacing it buys nothing
and is one more identifier crossing the boundary.

A NULL `entity_id` on either side means *unstamped* — a pre-multi-entity row, or a
tenant that never used entities — not "some other entity". Treating unknown as
cross-entity would redact the useful detail for every single-entity tenant, the
overwhelming majority, to protect a boundary that does not exist for them. When
both a same-entity and a cross-entity match exist, the headline is built from the
same-entity one (the viewer can act on it and needs the identifiers) with a bare
count of the others appended.

The Exception stays payment-blocking on a cross-entity hit. That is intended: a
suspected intra-group double-bill is exactly the case that wants human sign-off.

## 41. A run's status is derived from its payments, not read from the column

**Decided:** 2026-08-19 · `backend/app/services/payment_runs.py`

`PaymentRun.status` had exactly one writer after execution: the final rollup in
`_dispatch_run_payments`. Neither the processor webhook, the reconciler backstop,
nor `/compliance/{release,dismiss}` touched it. A run that rolled up `submitted`
because one payment was held `pending_compliance`, and then had that payment
dismissed, kept reporting `submitted` while its own payments said `failed` —
`/retry-failed` 409'd on `RETRYABLE_RUN_STATUSES`, and `/execute` and `/resume`
409'd on the claim states. A dead end, and precisely the "button that can't act"
the `retryable_failures` field exists to prevent.

Two mechanisms were on the table. Recompute-and-persist at every site that
changes a payment's status is the obvious one, and it is what a schema-first
instinct reaches for; it was rejected as the *primary* mechanism because it is a
completeness obligation on a set that grows — every future path that moves a
payment must remember to call it, and the failure mode of forgetting is silent
and identical to the bug being fixed. Deriving on read cannot be forgotten:
`derive_run_status(persisted, rollup)` is the single rule, and the runs list, the
run detail and the retry gate all route through it, so what an operator sees and
what an endpoint gates on cannot diverge by construction.

The split is between *claim* states and *outcome* states. `draft`, `executing`
and `cancelled` say something about who holds the run, not about how its payments
turned out, and `/execute` / `/resume` gate on exactly those — re-deriving them
would let a rollup un-claim a run mid-dispatch. They pass through untouched.
`submitted`, `partial`, `failed` and `completed` are claims about outcomes, and
outcomes are what the payment rows already record, so those are recomputed.

`recompute_run_status` was kept as a secondary, belt-and-braces write at the three
endpoints that move a payment outside the dispatch pass. Not because the reads
need it — they don't — but because a stored column that lies is a trap for
anything reading the database directly: an operator at `psql`, a CSV export, a
future consumer written by someone who reasonably assumes the column means what
it says.

The rollup's own default was fixed in the same pass. It returned `completed`
whenever nothing was completed, failed or in flight — a fail-open answer on a
money-run status, which made a run with every payment still `pending` (nothing
attempted) and a run with no payments at all both report success without a cent
moving. All-pending is the resumable state and now reports `executing`; no
payments at all reports `draft`.

That first pass left the *default itself* fail-open — `completed` was still the
fallthrough for any payment status no bucket named. A later pass closed it: a
`voided` payment (a human reversing one after the fact) joined
`RUN_PAYMENT_FAILED_STATUSES` beside `cancelled`, and the final rung now returns
`completed` **only** when every active payment completed — otherwise `partial` /
`failed`. So a run of all-voided payments reports `failed`, not `completed` with
`payments_completed: 0`, and a future adapter status can't slip through as
success either.

## 42. The corridor-quote optimizer got a caller, but not the one that routes money

**Decided:** 2026-08-19 · `backend/app/services/corridor_quotes.py`, `backend/app/api/payments.py`

`compare_quotes` shipped fully built, documented and unit-tested with nothing
calling it. Unreachable code on the money path is worse than absent code: it
passes review, accrues no test pressure from real callers, and hands whoever
wires it up every untested assumption at once. Two very different things were
being called "wiring it up", and conflating them is why it sat unwired for so
long.

The first is letting the auction *decide* which rail moves the money — pick the
cheapest bid at dispatch time and send the payment there. That is a treasury
policy decision, not a defect fix. Which bank moves a customer's money is a
question about banking relationships, settlement risk, reporting and existing
contracts; the fact that one adapter reports a lower fee is an input to it, not
an answer. A bug-hunting pass does not get to make that call, and making it
silently — inside `_execute_single_payment`, where the change would be small —
would be the worst version of making it.

The second is letting a *human* see the comparison. That needs no policy call at
all, and it is what shipped: `POST /api/payments/corridor-quotes`, read-only,
advisory, booking nothing. The response carries `advisory: true` so a client
cannot mistake it for a routing decision, and `payment_corridor.pick_corridor`
remains the sole authority over the rail on a `Payment` row.

This is the general shape for "built but unreachable" capabilities: find the
largest slice that is safe to reach without deciding anything nobody has decided,
wire that, and leave the policy question explicitly open rather than answering it
by omission. The alternative — deleting the module as dead code — would have
thrown away the correct part along with the undecided part.

The same test was applied to `services/financing_adapters` and it failed:
supply-chain financing has **no safe read-only half**, because a financing quote
is only meaningful if it can be accepted, and accepting it moves money to a
supplier from a third-party financier. That one stays unwired, deliberately.

## 43. The commitment set takes one schedule per invoice, and `id` is only a determinism tiebreak

**Decided:** 2026-08-19 · `backend/app/api/analytics.py`

`_commitment_rows`' `outerjoin(PaymentSchedule)` fanned an invoice out to one row
per schedule, double-counting it at full amount across the forecast, the
cash-position curve, the what-if, every copilot tool and the `plan_id` hash
simultaneously. Dedup takes the **latest** schedule via `DISTINCT ON (invoice_id)
… ORDER BY invoice_id, created_at DESC, id DESC`, matching
`discount_auto_trigger._resolve_due_date`'s existing `ORDER BY created_at DESC
LIMIT 1` on the same table so the discount engine and the cash forecast cannot
disagree about an invoice's due date.

`created_at` defaults to `transaction_timestamp()`, which is constant within a
transaction, so two schedules written in one commit carry the *same* timestamp and
"latest" is genuinely undefined between them. The `id` tiebreak exists solely to
make that case **deterministic** — a copilot `plan_id` is a hash of its resolved
inputs and must not flap between reads — not to assert an order the data does not
carry.

Rejected: summing every schedule's terms (a schedule is a restatement, not a
tranche) and adding a real sequence column (a migration for a column no writer
populates today).

## 44. A conformance claim is emitted only when the conformance check passes

**Decided:** 2026-08-19 · `backend/app/services/e_invoice/bis3.py`

`cbc:CustomizationID` / `cbc:ProfileID` are not decoration: together they assert
that a UBL invoice conforms to EN 16931 as profiled by PEPPOL BIS Billing 3.0. An
Access Point routes on them and validates the payload against that profile.
Emitting them on a document that does not meet the profile is worse than emitting
nothing — it converts a document the receiver could have read as plain UBL into
one it is obliged to reject, and it makes our own logs claim a compliance posture
we do not have.

The send path was already declaring BIS 3.0 on the AS4 envelope for documents
with no `cbc:EndpointID` on either party, tax subtotals with no amounts, and lines
with no VAT category. So the document was brought up to the profile *and* the
claim made conditional on it: `generate_ubl` declares the identifiers only when
`bis3_conformance_errors(doc)` is empty, and `peppol_send` refuses to transmit a
document it can itself disprove.

Rejected: adding the two strings unconditionally — the cheap change, and the one
that makes the lie load-bearing; and gating on nothing while leaving the AS4
envelope claiming the profile — the status quo, which only fails at a real Access
Point, in production, on a customer's invoice.

The check is honest about its own limits. It is a mandatory-element pass over the
rules the normalized model can answer, not the official Schematron; a document
that passes may still fail the real validator, but one that **fails** provably
does not conform, and that asymmetry is exactly what makes a conditional
declaration sound.

## 45. CFDI states `ObjetoImp="03"`, not `"01"`, when a taxed line's rate is not establishable

**Decided:** 2026-08-19 · `backend/app/services/e_invoice/country_formats/cfdi.py`

SAT's `c_ObjetoImp` values are claims about the line, and its validation rules key
off them: `"02"` (subject to tax) *requires* the per-line `cfdi:Traslado`; `"01"`
(not subject to tax) and `"03"` (subject, not obliged to break it down) forbid it.
The generator stamped `"02"` on every line and emitted no Traslado at all.

The obvious repair — fall back to `"01"` when the breakdown cannot be built —
trades one false claim for another: a line carrying a tax amount is not *no objeto
de impuesto*. So the mapping is three-way: `"02"` + a complete Traslado when
`Base` and a rate are establishable, `"03"` when the line is taxed but the rate is
not (a multi-rate document, or a tax amount with no rate anywhere), `"01"` only
for a genuinely untaxed line.

Related: a zero rate is emitted as *tasa cero* (`TipoFactor="Tasa"`,
`TasaOCuota="0.000000"`, `Importe="0.00"`) and never as `"Exento"` — the two are
different claims and the normalized model cannot distinguish them.

## 46. Outbound notification legs run after the caller's commit, not inside it

**Decided:** 2026-08-19 · `backend/app/services/post_commit.py`

`transition_invoice` awaited the whole notification fan-out — one email per
recipient, serially, then a Slack / Teams POST with a 10-second `httpx` timeout —
while the caller's transaction was still open. `payment_erp_sync._sync_one_payment`
takes the invoice `SELECT … FOR UPDATE` and only commits after the transition
returns; `review.approve_invoice` holds `FOR UPDATE` on the `WorkflowInstance`. A
hung chat webhook therefore held a row lock on a live invoice for its full
timeout, and N recipients multiplied the email leg linearly.

`services/post_commit` queues the transports on the caller's *session* and fires
them from SQLAlchemy's `after_commit`, which runs after the DB has committed — so
every lock is already released, and no call site had to change. The in-app
`Notification` rows deliberately stay in the caller's transaction: they are DB
writes and should commit atomically with the status change.

Rejected: parallelising the email leg with a bounded `gather` (shrinks N×T to T
but leaves the lock held for T, so it treats the symptom); and threading an
explicit post-commit step through all ~35 `transition_invoice` call sites
(invasive, and one missed call site silently reverts to the old behaviour).

Accepted consequence, and the right one: **a transaction that rolls back now sends
nothing** — we no longer email people about a status change that never happened.
Under a `dispatch_engine_scope` the jobs are awaited inline via `await_only`
rather than spawned, because that worker's loop closes the moment the job returns
and would abandon a task mid-send; inline still costs no lock, only latency on a
background worker.

## 47. One component owns the tinted badge; a caller names a tone, not a colour

**Decided:** 2026-08-19 · `frontend/src/lib/components/ui/Badge.svelte`

202 CSS rules hand-rolled the tinted-badge recipe as an `rgba()` tint plus a
literal hex — 44 distinct spellings of five tones. Every one passed the contrast
guard, so this was design-system debt rather than a defect; but the same tone
written four ways is precisely how the 29 sub-4.5:1 badges accumulated unnoticed
(§30). A shared primitive is the structural fix that stops the next 29.

`Badge.svelte` is now the single owner: a caller names a **tone** and cannot
spell it wrong. `variant` passes the caller's semantic class through as a
**selector hook only** — the e2e suite reads `.badge.approved` — and never as
colour, so styling and test-targeting stop being the same mechanism.

Sizing is fixed rather than exposed as a prop. Call sites varied padding by a
pixel with no discernible intent, and one size is most of the point of a shared
primitive; a pill that genuinely needs different metrics is a different
component, not a prop. `ScreeningBadge` keeps its dense inline metrics but takes
the palette tokens, which is the shape that concession should have.

Two tones stay deliberately non-tinted. `neutral` is a flat `--bg` chip standing
for the *absence* of a signal, and tinting it would give "nothing to report" the
visual weight of a status. `erp` is a measured purple literal that shares no
semantics with the other five.

**Rejected: converting all 125 badge-shaped rules in one pass.** The tokens
standardise on alpha `.15`, so normalising a `.1` or `.12` rule visibly
*strengthens* that badge — a real visual change, not a refactor. Landing it
wholesale would make any subsequent visual complaint unattributable to a
specific tranche. Half moved in the first tranche; the rest follows in
attributable batches, with collapsed distinctions checked each time (two were
verified here: recurring's `paused` / `ended` greys, and three punch-out ambers).

## 48. A counts endpoint takes every list filter except the one it is tallying

**Decided:** 2026-08-19 · `backend/app/api/purchase_orders.py`, `backend/app/api/vendors.py`

`GET /api/vendors/counts` exists because a status chip that counts only the
current page undercounts the moment a tenant has more rows than fit — the
Unverified attention badge was the original case. `GET /api/purchase-orders/counts`
is the same fix for the same reason, and copies it deliberately rather than
inventing a variant.

The non-obvious part is which filters a counts endpoint accepts. It takes
`search` and every *narrowing* filter the list takes — the tallies have to
describe the same population the list would render, or the chips disagree with
the table under them. It deliberately does **not** take `status`, because status
is the dimension being tallied: applying it would return the selected status'
count and zero for every other chip, which is exactly the "chip that lies"
failure the endpoint exists to prevent.

RBAC matches the list endpoint exactly rather than being tightened. A counts call
that 403s where the list 200s produces a page that renders rows above chips that
cannot explain them, which is a worse failure than the count simply being
unavailable — and the frontend already latches an unavailable count back to
pre-counts behaviour.

**Amended 2026-09-04 — the contract is now enforced, and the RBAC clause runs
both ways.** All three parts of the above are asserted by
`backend/tests/test_whole_set_kpi_rollups.py`, which discovers `/counts`
surfaces off the mounted OpenAPI schema (and each handler off its generated
`operationId`) so a new one is covered without editing the file. Stating the
contract was not enough: three violations were found the day the guard was
written — the payments History chips tallying the whole entity while the list
was searched, the vendors chips hand-rolling a predicate set that had already
diverged on `source`, and the purchase-order pair running two copies of the
same predicates that disagreed on a malformed `vendor_id` (a 400 from one, a
500 from the other).

The RBAC clause is checked in **both** directions, which the original entry did
not spell out. It argued the tighter case (a tally a reader of the rows cannot
reach). The looser case is worse and had actually shipped:
`GET /api/vendors/change-requests/counts` admitted `ROLE_CFO` while its queue
does not, so a role deliberately excluded from the dual-control bank-change
review could still read how many redirects were staged. A tally reachable by
more callers than the rows it counts discloses the size of a set they cannot
see — for a fraud-review queue that is the whole point of the exclusion.

One exemption is legitimate and is recorded in the guard rather than assumed: a
list offering **no** narrowing filter has no predicate set for a shared builder
to protect. The change-request queue is the only such surface today, and the
exemption fails if that queue ever gains a real filter.

## 49. A blocked row names the exception type, never the exception's description

**Decided:** 2026-08-19 · `backend/app/api/payments.py`, `backend/app/services/payment_runs.py`

`GET /api/payments/queue` offered rows that `POST /api/payments/runs` then hard
409'd, because run creation refuses any invoice carrying an unresolved exception
in `PAYMENT_BLOCKING_EXCEPTION_TYPES`. The queue now marks each row `blocked` with
a `blocked_reason`.

Two calls in that. First, the predicate is **not restated**: a new
`blocking_exception_types()` is the single source, and `blocked_invoice_ids` was
redefined in terms of it, so the queue and the gate cannot drift and a new
blocking type reaches both for free. That mattered immediately —
`payment_reconciliation` was added to the tuple earlier in this same branch (§41's
aged-out-payment fix) and required no second edit.

Second, `blocked_reason` is the exception **type** from a fixed vocabulary, never
the exception's `description`. A description is free text assembled from invoice
and vendor data; this field crosses a JSON boundary to an operator's browser, so
shipping it would put vendor names and amounts into a payload whose whole purpose
is "this row is not actionable". The type is enough to explain the block, and a
user who needs the detail opens the exception where the normal scoping applies.

Where an invoice carries several blocking exceptions the earliest tuple member is
reported, so the reason is deterministic rather than dependent on row order —
a flapping reason string on a payment surface reads as a bug in the gate.

## 50. The email-shape check has one owner, and it ends in `\Z`

Three copies of `^[^\s@]+@[^\s@.]+(?:\.[^\s@.]+)+$` lived in `api/signup.py`,
`api/partner.py` and `app/schemas/scheduled_report.py`. Three copies of a
validation rule drift, and this one gates **who receives a tenant's AP data by
email**. It is now `app/utils/emails.py::looks_like_email`, with a test that
pins the admitted cases so a later tightening has to edit a failing test rather
than silently narrow who can be mailed.

Hoisting it found a live hole. All three ended in `$`, which in Python matches
end-of-string **or just before a trailing newline** — so `"user@example.com\n"`
passed every check and was stored as a login, a child-tenant admin address and a
scheduled-report recipient. A newline reaching an SMTP header is the
header-injection primitive. The shared pattern anchors with `\Z`.

Rejected: pulling in `email-validator`. RFC-strict syntax buys rigour nobody
wants here (quoted local parts, address literals) and still answers nothing about
deliverability, which the confirmation round trip already answers. The
permissiveness is deliberate; the trailing newline was not.

## 51. "Today" is UTC across the backend, and the guard scans four spellings

`date.today()` resolves in the **server's** local zone. Latent on a UTC container
— the deployed shape — and live on any host that is not. Every site under
`backend/app/` now goes through `app/utils/dates.py::utc_today()`.

The sites that mattered were not the stamps. `select_tier_for_date(as_of=…)`
decides whether an early-pay discount is still claimable, and the buyer's view,
the supplier's portal view and the auto-capture sweep read it from two different
clocks — so for part of each day the three could disagree about the same offer.
The recurring-template `generate-now` path derives the period key that **is**
`uq_invoice_recurring_period`, so a manual generate near midnight resolved a
different period and produced a second payable. `Invoice.approval_date` is a
regulated SOX field on all three approval paths.

Rejected: converting only the money-comparison sites and leaving stamps and
filenames local. Each stamp sits beside a `datetime.now(UTC)` in the same
artifact, so a split makes one document disagree with itself —
`export_aging_snapshot` was already labelling a CSV with one date while its
buckets meant another.

Rejected: a `TZ=`-manipulating behavioural test. `date.today()` reads the real
clock, so such an assertion is only true for part of the day. The AST guard is
the right layer — but it had to be fixed first: it matched only `ast.Name`, so
the attribute-shaped `datetime.date.today()` was invisible, and that is exactly
what both Positive Pay modules used. Either could have been certified "converged"
while still reading local time. The scanner now also catches naive
`datetime.now().date()`, which is local and is not spelled `today` at all.

## 52. A caller-specific emphasis is a wrapper, not a sixth badge tone

`pending_compliance` shares the `warning` tone with `pending` on purpose — both
are waiting — and the **ring** is what says "a human must clear this". Converting
those pills to the shared primitive (§47) threatened to flatten a distinction the
code explicitly documents. It survives as a non-inset `box-shadow` on a
caller-owned wrapper, which also takes no layout space and so removes the
misalignment the old `inset` shadow was itself working around.

Rejected: a `ring` / `emphasis` prop on `Badge` — a one-caller prop is exactly
what §47's fixed-sizing argument refuses. Rejected: a `:global(.badge.…)` rule —
this repo has zero `:global` in routes or components.

The same conversion exposed the inverse of §47's warning. The tokens standardise
on alpha `.15`, and that "visibly strengthens" a badge converted from `.1` — but
it also **weakens anything nested inside it**. `--text-muted` sub-labels within a
tinted chip (`.card-meta`, `.discount-pct`) fall to 4.34:1 and 4.11:1 purely
because their parent got the standard tint. Neither guard would catch it:
`cssAudit` deliberately resolves no cascade, and axe only sees whichever tab
renders. They now take `color: inherit`.

## 53. A list search never filters the rows already loaded

`/requisitions` and `/expenses` filtered client-side over the loaded page, with
an honest empty state explaining that a match further in was not visible. The
copy was good; the limitation it described was the bug. The term now goes to the
server, and the filter and its empty state are gone — the table, the footer count,
Load-more and the KPIs finally describe the same set.

Rejected: keeping a client filter alongside the server one, which would leave two
divergent notions of "matching" on one page.

The fix reintroduced issue #168 and had to be caught: `load()` is called
**synchronously** from the status-filter `$effect`, and Svelte tracks reads
transitively through called functions — so reading `search` there made the effect
depend on the term. Every keystroke fired an immediate request, and because the
loader stamps the applied term before the debounce timer fires, the debounce then
short-circuited and the un-debounced request was the only survivor. Typing five
characters cost six requests. That is the second time this has landed on these
pages through a different function (`syncUrl` first, now the loader), so the rule
is recorded as a property of the **call site**, not of one function: anything an
effect calls synchronously is inside its tracking scope. A `fill()`-based e2e is
structurally blind to it — one state write, one term — so the guard has to type.

## 54. Persisting a cash plan does not weaken the stateless `plan_id`; it is keyed by it

§5/§6 gave plans no table deliberately: `plan_id` is a pure hash of resolved
inputs plus the calendar date, and enactment re-derives everything. That still
holds — nothing on the enact path reads `cash_plans`, and `payment_runs.plan_id`
is still the draft-run anchor. What a stored row adds is the one thing
re-derivation cannot supply: **what the projection said on the day it was made**.
Yesterday's plan is not recomputable — the horizon starts elsewhere and the
invoices have moved — so a variance with no baseline is just a restatement of the
present.

Rejected: upsert-on-save. Restating a snapshot against newer data rewrites the
very number the comparison measures against, so a repeat save returns the
existing row untouched (`created: false`, 200), with `uq_cash_plans_org_plan_id`
holding that under a concurrent retry.

Two rules keep the variance honest. Only **fully-elapsed** periods are scored: a
period whose window has not closed has a partial actual, and subtracting a whole
projection from it manufactures an underspend that reverses by week's end. Open
periods are still returned and labelled, never silently zeroed — and pro-rating
was rejected, since it invents a within-period spend distribution the plan never
asserted. And a `completed` payment with no `completed_at` is **counted on
`undated_payment_count`, not dated by a proxy**; its `created_at` bounds whether
it is in scope, never which period it lands in.

## 55. Consolidated scope is discovered from the plan id, not declared by the client

`plan_id` already hashes the `entity_id` it was built under, so exactly two ids
can be legitimate for a caller: their selected entity's, and the consolidated
one. `_resolve_and_verify_plan` tries both, most specific first.

Rejected: a `consolidated: bool` on the replay body. The plan card is rendered
from a tool result that carries no entity, so the flag would be a claim we would
have to trust, and it would need threading through `CopilotChatMessage`.

Discovery widens nothing. Entity scoping is a **view** scope — `get_entity_id`
validates the header against the tenant's own `entities` table and grants no
access by itself — so the consolidated id is equally reachable by omitting
`X-Entity-ID`, and a tampered parameter still matches neither candidate.

One consequence had to be followed through: a consolidated draft run previously
took its entity from `get_write_entity_id`, which would stamp a cross-entity run
with whichever subsidiary happened to be selected. The run entity now derives
from the plan's own scope — the selected entity for a scoped plan, the tenant's
default for a consolidated one — matching what an entity-less
`POST /api/payments/runs` already does.

---

## 56. The fixture-adapter fallback is gone from every remaining registry

**Decided:** 2026-08-21 · `backend/app/services/{card_adapters,positive_pay_adapters,enrichment_adapters}/dispatcher.py`

§29 removed "a named unknown provider resolves to `mock`" from payments, ERP and
FX; §36 from sanctions. Three registries still had it, and in each the fixture is
exactly the confident-wrong-answer §29 describes:

| Family | What the fallback returned | What a typo'd config produced |
|---|---|---|
| cards | `create_card` → `success=True` + `mock_card_…` / `4242`; `get_card_details` → the PAN `4242424242424242`; `cancel_card` → `True` | every issuance "succeeded"; the run's card leg marked payments `completed` and invoices `payment_scheduled`; vendors were emailed reveal links to a fixture PAN |
| positive pay | the `csv` formatter, under the requested `bank_format` | a CSV file the bank cannot parse, stored + audited + idempotency-slotted as the requested layout — so a fraud control the tenant believed was in force was not |
| enrichment | fabricated firmographics (legal name / address / DUNS / employee count) with `matched=True` | an invented identity presented as a D&B lookup, one click from `POST .../apply` writing it onto a real supplier's *screened* `name` |

Same rule, same shape: **no configured provider still means the default**
(local-first, guard rail 7); a **named** one we have no adapter for raises. And,
as §29 established, each caller decides what the refusal means — the card table
is in `backend/docs/virtual-cards.md` § Provider resolution.

One addition beyond §29's pattern: `get_card_adapter` now imports its own
built-in adapters. A refusal is only trustworthy if every adapter has had a
chance to register, and relying on each call site's `# noqa` import preamble made
that a property of the caller rather than of the dispatcher.

---

## 57. The two billing meters are tenant tables; `assistant_usage` is not

**Decided:** 2026-08-21 · `backend/app/services/extraction.py`, `backend/docs/database.md`

`extraction_usage` and `card_rebates` read like control-plane data — they meter
the platform's own billing, which is keyed by org — and three docstrings plus
`docs/database.md` said they were. They are not, and never have been: neither is
in `tenant_provisioning.CONTROL_TABLES`, no Alembic revision creates them, and
`scripts/seed.py::create_control_tables` filters to the control set. They exist
only where `provision_tenant` puts them, per tenant, which is also where
`services/billing`'s `rollup_usage` reads them.

`services/extraction.py` was written against the claim rather than the schema and
committed the meter through a control-plane session. `to_regclass` is NULL there,
so the INSERT raised `UndefinedTableError` *inside* `run_extraction`'s own `try` —
its handler rolled the tenant transaction back, opened an `extraction_failed`
exception and transitioned the invoice to `failed`. A successful extraction was
recorded as a failure, and billing's primary meter stayed permanently zero.

The meter now rides the tenant session and its existing commit, so it lands with
the extraction result or not at all, and the `ctrl_db` parameter that existed
solely for this write is removed rather than left available to a future caller.

Rejected: adding the tables to `CONTROL_TABLES` with a control-plane migration.
That direction additionally requires repointing `api/billing.get_subscription` at
`control_db` and migrating every existing tenant's rows, and it would split the
two meters across two databases for no gain — `rollup_usage` already reads them
together, per tenant.

`assistant_usage` was checked against the same question and is genuinely
control-plane (it *is* in `CONTROL_TABLES`, present in `feohledger`, absent from
tenants). The asymmetry is real, so it is written down here rather than left to
be re-derived — and `docs/conversational-assistant.md` no longer cites
`extraction_usage` as its precedent.

**Guard:** `tests/test_extraction_usage_placement.py` asks Postgres where the
table is, both ways round. The file it replaced asserted the broken placement in
all eight of its tests, using an `AsyncMock` control session — so the INSERT was
never executed against a real database, which is why this survived.

---

## 58. `settings.mfa.required` is accepted even when `FEOH_MFA_ENABLED` is off — the inertness is reported, not refused

**Decided:** 2026-08-22 · `backend/app/api/organization.py`

Saving Org Settings → Security → "Require two-factor authentication for all
users" writes `settings.mfa.required=true`, but MFA itself only runs behind the
platform master switch `FEOH_MFA_ENABLED` (off by default — guard rail 7). Before
this decision, an admin could save the toggle with no signal anywhere that it
was currently inert: no user would ever be prompted.

Two options were on the table: refuse the save with a 409, or accept it and
surface the mismatch. Refusing is wrong here because the config isn't invalid —
an admin pre-configuring `required: true` ahead of a deployed environment
flipping `FEOH_MFA_ENABLED` on is a legitimate, common sequencing (the same
tenant settings travel with the org across environments; nothing about the
*setting* is malformed). This is the same shape of problem §34's residency
`alignment` block and `extraction.py`'s `platform_provider_reason` already
solved: accept the config, and answer "is it actually in effect right now?" as
a separate, computed-on-read signal.

`PATCH /api/organization` now logs a loud warning when accepting
`mfa.required=true` while the switch is off, and `_org_response` (shared by
`GET` and `PATCH`, mirroring `_residency_response`) injects
`settings.mfa.enforcement_active` — `required AND FEOH_MFA_ENABLED` — computed
fresh on every read, never persisted. The frontend toggle isn't wired to the new
field yet (tracked in `docs/followups.md`); the API contract is what this
decision fixes.

---

## 59. Ambiguous-date day-first preference reads a Companies-House number, not the data-residency region

**Decided:** 2026-08-25 · `backend/app/utils/dates.py`

Four call sites — `services/extraction.py`, `services/csv_import.py`,
`services/bank_reconciliation.py`, `services/vendor_statement_recon.py` — each
hand-rolled their own try/except order for a numeric date like `03/04/2026`,
and all four tried `%m/%d/%Y` before `%d/%m/%Y`. A UK invoice dated 3 April
booked silently as March 4th — no error, just a wrong date. `03/04/2026` is
genuinely ambiguous (both readings are structurally valid), so fixing the
order can only trade which locale gets corrupted; the real fix needs a signal
from outside the string.

Two existing org-configured signals could have supplied it:
`settings.residency.region` (`us`/`eu`/`uk`/`ca`/`au`, §34's GDPR/CCPA data
pin) and `settings.company.companies_house_number` (added alongside
`vat_registration_number` for a different UK-persona finding in this same
round). Residency was rejected: it answers *where this tenant's data is
legally required to live*, not *what country this company operates in* — a US
company can legitimately pin EU residency for a subsidiary's data, and reading
that as "this org is day-first" would corrupt a US company's dates on an
unrelated legal configuration. `vat_registration_number` was also rejected: VAT
registration is common across the EU and beyond, so its presence doesn't imply
UK, or even which day-first country. `companies_house_number` is the one
signal that means UK and only UK — Companies House registers UK entities
exclusively — so `resolve_day_first_preference` reads that field alone. No
signal (the default for every org that hasn't set it) resolves to
`day_first=False`, preserving the pre-existing month-first reading for the
common case.

The disambiguation itself (`parse_ambiguous_date`) is a second, orthogonal
piece: given `day_first`, try the preferred order, fall back to the other only
when the preferred order is structurally invalid for that string, and return
`None` — never guess — when neither parses. It is intentionally narrow (only
the `M/D/Y`-vs-`D/M/Y` slash/dash shape); ISO, `YYYY/MM/DD` and dotted
(`DD.MM.YYYY`, conventionally day-first by SEPARATOR, not by org locale) forms
are unambiguous and stay in each caller's own format list.

**Guard:** `tests/test_ambiguous_dates.py` unit-tests both functions and
AST-scans the four call sites (mirroring the UTC-today convergence guard in
`tests/test_utc_today.py`) so none of them can quietly regrow a hardcoded
`"%m/%d/%Y"` / `"%d/%m/%Y"` pair outside the shared helper.

## 60. An IdP-supplied email is refused for a control character, not held to the full shape rule

`services/identity_provisioning.extract_and_check_email` is where both SSO
protocols normalise the address an IdP asserted. It lower-cased, stripped and
domain-allowlisted it, and said nothing about its shape. `.strip()` removes a
*trailing* newline, so the obvious case was incidentally safe; an **interior**
one survived untouched and was stored as `User.email` — a login, and the
destination of every notification the app sends that person. A newline in a
value that reaches an SMTP header is the header-injection primitive, and the
attacker-chosen continuation writes itself: a `Bcc:` on mail carrying that
tenant's AP data.

The obvious fix — run `looks_like_email`, the rule §50 gave one owner — is the
wrong one **here**, and this is the decision. That rule requires a dotted
domain. A corporate IdP can legitimately assert `user@intranet`, and refusing it
would lock a whole tenant out of its own workspace over a cosmetic rule that
has nothing to do with the exposure. Trading a header-injection risk for a
guaranteed lockout is not a trade.

So the guard is narrower than the shape rule and exactly as wide as the danger:
`utils/emails.is_header_safe` refuses C0 controls and DEL, nothing else. No IdP
has a legitimate reason to emit one, so there is no lockout to weigh against it.

Two smaller calls inside that:

- **Refused, not sanitised.** Stripping the character out would provision a
  *different* identity than the one that signed in, silently. `UnsafeEmailAddress`
  propagates and both callbacks turn it into their existing generic refusal
  (SAML `_fail("unsafe_email")`, OIDC a 403 + a PII-free audit reason) — the
  address itself is never echoed into a second log line, which is the whole
  point of the value being unsafe.
- **The check runs before the allowlist**, so an unsafe address whose domain
  happens to be allowed is still refused.

Both callbacks are public routes, so an unhandled raise would be a 500 — a
worse failure than the one being fixed. `tests/test_sso_email_safety.py`
AST-scans each for the `except` rather than trusting the two edits to stay.

Found by the `test_email_shape_call_sites.py` drift guard, which asks which
modules decide "is this an email" without the shared rule. Its other two hits
are exempt with reasons: `api/enrichment.py` extracts a host, and
`api/auth_saml.py` decides whether a NameID *is* an email before falling back to
the attribute statement — where the shape rule would send exactly the internal
domains above down the fallback path.

## 61. An unrecognised sanctions `result` is a hold, not an allow — the verdict is never reached by omission

`ScreeningResult.result` is a three-value contract — `clear` | `match` |
`review_required` — and `check_payment_compliance` branched on two of them:

```python
if screening.result == "match":       # → refuse
if screening.result == "review_required":  # → reason → hold
# anything else falls through
verdict = "hold" if reasons else "allow"
```

Any fourth value therefore matched neither test and fell through to `allow`,
which is the one verdict that must never be reachable by *omission*. A
sanctions gate that clears a name because it could not read the answer is worse
than one that never ran: the payment goes out carrying the audit row of a check
that reported nothing.

The fix is an explicit `elif screening.result != "clear"` — `clear` is the only
value that proceeds silently, and everything else adds a reason.

**Why `hold` and not `refuse`.** Identical reasoning to the unknown-PROVIDER
path (§36) and deliberately the same verdict: the payment waits in
`pending_compliance`, the caller opens the `payment_compliance_hold` exception,
and the misconfiguration reaches the AP queue rather than the payment rail. A
`refuse` would be a dead end — `/compliance/release` re-runs the same gate, so a
refusal on an unreadable value could never be cleared by a human.

**Why fix a latent bug.** No shipped adapter emits a fourth value today, so this
was unreachable in practice. It is fixed anyway because the three live provider
adapters (ComplyAdvantage, Dow Jones, Refinitiv) are fail-closed skeletons
awaiting credentials: the first real provider response shape we have never seen
would arrive precisely here, and it would arrive as a silent allow. The
comparisons are exact, so a provider differing only in case (`Clear`,
`REVIEW_REQUIRED`) is exactly as unreadable as one inventing a new word — the
guard covers those too rather than case-folding, because guessing at an
unreadable answer is the failure being removed.

`vendor_screening.screen_vendor_record` was already fail-closed for the same
input (`_STATUS_MAP.get(result, "review")`), so this aligns the payment gate
with the vendor gate rather than introducing a new posture.

Confirmed one of the eight "unverified leads" recorded in `docs/followups.md`
from the round-14 money-path hunt, which the tracker (#321) carried as a
hypothesis rather than a finding.

## 62. A rebate figure names its currency; the entity scope depends on who is asking

**Decided:** 2026-09-04 · `backend/app/services/currency_conversion.py`, `app/api/dashboard.py`, `app/api/payments.py`, `app/api/cards.py`, `app/api/analytics.py`, `app/services/billing/usage_rollup.py`

`card_rebates` carries **no currency column**. A rebate's denomination is only
knowable through the `virtual_cards` row it accrued on, and six rollups needed
it — five of them shipped as bare cross-currency `SUM(CardRebate.amount)`:

| Rollup | What the mixed figure was |
|---|---|
| `GET /api/dashboard` | the "Rebates Earned" KPI on the main page |
| `GET /api/payments/summary` | `total_rebates`, under the `"currency"` the same response declares two keys below |
| `GET /api/cards/dashboard` | the rebate cards + YTD breakdown (fixed earlier; the join it needed is what made the rest reachable) |
| `GET /api/cards/rebates` | the list's `total`, above per-row amounts that each state their own card's currency |
| `GET /api/analytics/cfo` | the rebate-yield **numerator**, divided by a reporting-currency denominator and then annualised |
| `services/billing/usage_rollup` | `card_rebate_total`, a billing meter a later slice prices |

`currency_conversion.card_currency_sql` is the one owner of the expression. It
is named for the *card*, not the rebate, because card figures (`amount_limit` /
`amount_charged`) read it directly while rebate figures reach it through the
join — one expression, two jobs.

**Filter, don't convert.** `total_paid` / `total_pending` convert through
`payment_reporting_amount_sql`; a rebate cannot, because there is no rate on the
row to convert with. So each single-figure rollup keeps the matching rows and
counts the rest onto an `excluded_rebate_count` — all four of them, including
the analytics rebate-yield leg, where it matters most: a numerator missing most
of the rebates yields a silently understated `yield_pct` that is then
*annualised*. That is the same "be honest about what could not be combined"
rule §18 established for read-time FX.

The disclosure has to reach the wire, not just the handler.
`DashboardResponse` had no `excluded_rebate_count` field, so `response_model`'s
default `extra="ignore"` dropped the key the handler returned and the dashboard
KPI was right-but-silently-partial for every real caller — the state this whole
entry argues is worse than the original wrong-but-complete figure. A test that
calls the handler and reads its dict cannot see that; the guard is an
HTTP-level assertion. Converting on a read would make a historical figure move under the reader.

**Two things that look inconsistent and are not.**

*The entity scope differs by caller.* `GET /api/payments/summary` and the
dashboard KPI are entity-scoped; the billing meter is deliberately org-wide.
Same table, different question: the first two sit beside entity-scoped outflows
an operator reconciles them against, while the platform bills the customer
**org**, so a subsidiary breakdown there would be the wrong unit. A future
reader "fixing" the billing meter to match its siblings would be introducing a
bug.

*The billing meter groups where the endpoints filter.* An endpoint reports one
headline figure, so it picks a currency and discloses the remainder. A meter
cannot: there is no rate that turns a mixed scalar into a charge, and dropping
the non-reporting currencies would silently drop billable activity. So the
currency goes in the meter **name** — `card_rebate_total.USD` — one key per
currency, always, and **no key at all** for an org with no rebates. Zero rebates
in an unstated currency is not a fact, and one shape beats two: a consumer reads
every key prefixed `card_rebate_total.` without having to know whether it is
looking at a single- or multi-currency org. `report_usage` iterates the meter
map generically, so no adapter changed.

**Two error swallows went with it.** The payments-summary and analytics rebate
queries each wrapped themselves in a bare `except Exception` returning `0`.
That was scaffolding for a since-fixed bug where the query ran against the
control plane, where `card_rebates` does not exist (§57 records that cause).
The table is absent from `CONTROL_TABLES`, so the case is unreachable — leaving
a swallow that turned any *other* failure into a confidently wrong money figure
under a response declaring a currency. Zero rebates and "we could not read the
rebates" are different claims, and no sibling figure on either surface swallows.

`tests/test_rebate_currency_denomination.py` guards the class rather than the
six instances: an AST scan fails any statement summing `CardRebate.amount` that
neither filters nor groups by currency, and a source scan fails any module that
re-derives the expression inline instead of calling the shared helper.

---

## 63. Every adapter registry is classified, and the fixture fallback is gone from the ones that could answer wrongly

**Decided:** 2026-09-04 · `backend/app/services/*/dispatcher.py`, `backend/tests/test_adapter_registry_fail_closed.py`

§29 removed `_REGISTRY.get(x) or _REGISTRY["mock"]` from payments / ERP / FX, §36
from sanctions, §56 from cards / positive-pay / enrichment. Six families still had
it, and every one of them was written **after** the rule existed — which is the
finding. The rule was a habit, reapplied by whoever remembered it, not a property
of the codebase.

A `mock` adapter is not an inert stub. It is the thing that makes `pnpm dev` work
with no cloud account, so it answers **yes** to everything:

| Family | What a typo'd provider name produced |
|---|---|
| 1099 e-filing | a `Tax1099Filing` row + a `tax_1099.filed` audit row + a 200 telling the org its 1099s were e-filed when nothing reached the IRS — and the `(org, idempotency_key)` slot burned, so the corrected retry returned `already_filed` and never filed either |
| TIN validation | `Vendor.tin_verified_at` stamped from a regex — the flag B-notice and 24% backup-withholding decisions key off |
| PEPPOL | a legally-significant e-invoice reported `sent` with a synthetic message id to a supplier that never received it, the row occupying `uq_peppol_one_live_per_invoice_direction` so the honest resend came back `already_sent` |
| tax rates | a jurisdiction VAT/GST figure computed off the in-repo fixture table while the response's `provider` field named the provider that was asked for |
| punch-out | a `PunchoutSession` the buyer is navigated to, and a PUBLIC cart-return endpoint accepting a different payload shape than the tenant's configured protocol — whose fixture cart converts into a real `PurchaseRequisition` |
| QMS | three fixture inspections resolved against the tenant's **real** POs and persisted as `completed` `QualityInspection` rows — the 4-way match's quality leg, so a fabricated `pass` clears the gate for whatever invoice references that PO |

The rule is unchanged: absent or empty still resolves the local-first default
(guard rail 7 — an org that has configured nothing is a normal state); a **named**
provider we have no adapter for raises; and each caller decides what the refusal
means. Across eight conversions the callers' answers fell into four kinds, which
is the useful generalisation: **refuse before a claim is recorded** (1099 filing,
above the idempotency-slot insert); **refuse before state moves** (TIN verify,
above the write that would otherwise *clear* a correct verification); **report it**
(the manual QMS route, the tax-rate routes); and **count it and hold what you
have** (the QMS sweep).

Three call-site details generalise past their own surface:

- **A refusal must not advance a cursor.** The QMS sweep's `last_synced_at` sits
  on the success path only. Advancing it on a refusal closes a window that was
  never pulled, so every inspection written during the outage is skipped *forever*
  once the config is corrected — worse than the fabrication the refusal prevents.
  Both the old fallback and a naive unguarded raise would have advanced it.
- **The inbound PEPPOL webhook returns a bodyless 503, not the route's usual
  opaque 204.** An unresolvable provider is *our* failure, not a decision about
  the document — §37's rule, not §29's. Acking would drop a supplier's invoice
  permanently; a 503 leaves it as work the Access Point redelivers.
- **The punch-out cart return drops silently (204) instead.** There is no
  retrying Access Point on that path — a supplier posts once from a browser — so a
  5xx surfaces a stack trace to a prober without recovering the cart.

**The durable part is the classification, not the conversions.**
`test_adapter_registry_fail_closed.py` now requires *every*
`app/services/*/dispatcher.py` to be classified — fail-closed, or fail-open with
what its fallback actually does written down — and AST-scans the fail-closed set
for the `.get(x) or <default>` / `.get(x, <default>)` shape. Sixteen of twenty-one
are fail-closed. The five that remain are a reviewed decision rather than a
backlog (`assistant`, `billing_adapters` — refused at boot by `main.py` for the
one dangerous path, `chat_notification_adapters`, `email_adapters`,
`embedding_adapters`), each re-checked against its current resolver *and* its
consumer. The admission rule is now written into the test: **a fallback belongs on
that list only if it cannot produce a confident wrong answer about money, a
document, or a control.** It must degrade to a no-op, a log line, or a
lower-quality suggestion.

**Not adopted, again: validating the provider name on write.** §29 rejected it for
`PATCH /api/organization` and the reasoning holds — settings predate any
validator, arrive from seeds and migrations, and an adapter can be *removed* from
the registry after a name was stored. The same argument disposes of allowlisting
`bank_format` at the schema: the route already funnels through `_require_formatter`,
which 422s naming the bad value and the registered alternatives, so a field-level
check would be a strictly narrower duplicate of the chokepoint every caller passes
through. The gap there was route-level *coverage*, and that test now exists.

---

## 64. `aws_textract` was the one adapter with no async client

**Decided:** 2026-09-04 · `backend/app/services/extraction_adapters/aws_textract.py`

Every adapter across the 21 registries reaches its provider over
`httpx.AsyncClient` except this one, because AWS ships no async boto3.
`boto3.client("textract", …)` resolves the credential chain (which can reach the
instance-metadata endpoint) and `analyze_expense` is a full HTTPS round trip
against a multi-second OCR service — both ran inline inside `async def`, holding
the event loop for that whole window while every other in-flight request on the
worker queued behind it.

Both call sites are exposed: `extract` is reached from the invoice upload route
**and the public email-intake webhook**, and `test_connection` is awaited directly
on the request path by `POST /api/organization/test-extraction`. The project
invariant grades a blocking call on a public webhook or auth path `Critical`.

Both now go through `await asyncio.to_thread(...)`, matching `services/storage`'s
`_put_object` and the three `*_dispatch` SQS sends. Client construction is factored
into its own `_client()` helper so the credential-chain resolution is offloaded
too, not just the round trip. `tests/test_sqs_dispatch_nonblocking.py` — already
the home of the boto3 loop rule — grew a thread-identity assertion per entry point
plus two AST scans (a blocking helper called inline from a coroutine, and an
inlined `boto3.client(...)` under any name).

---

## 65. A money filter bound is exact *and* snapped onto the column's own grid

**Decided:** 2026-09-04 · `backend/app/api/money_filters.py`

Typing the parameter `Decimal` was necessary but **not sufficient**, and that is
the part worth recording. Two separate roundings were in play:

1. **Python side.** `amount_min: float | None` followed by `Decimal(str(value))`.
   `Decimal(str(f))` recovers the shortest repr, so an ordinary two-decimal bound
   round-trips — which is why this never produced a visible bug — but the value had
   already been rounded to the nearest double before any application code ran.
   Declaring the parameter `Decimal | None` fixes this outright: FastAPI hands
   pydantic the raw query string, which parses exactly.
2. **SQL side, the non-obvious one.** SQLAlchemy types a comparison's bind
   parameter from the *column*, and the asyncpg dialect renders a bind cast:
   `invoices.amount >= $1::NUMERIC(15, 2)`. Postgres therefore rounds an
   over-precise bound **to nearest** at the column's scale — straight back onto the
   boundary row the bound was written to exclude. Retyping alone left the
   behavioural regression tests still failing; that is how this was found.

Rounding a bound to nearest is never right, because the comparison's *direction*
decides which way is safe. A money column is a fixed grid — `Numeric(15, 2)`, every
stored amount a whole number of cents — so the exact answer is to snap the bound
onto that grid in the direction of the comparison: a lower bound rounds **up** (the
smallest representable amount that still satisfies `>=`), an upper bound rounds
**down**. Exact for a bound of any precision. `money_filters.py` derives the scale
from the column itself rather than restating `2` at each call site.

Rejected: binding against a deliberately wide `Numeric(30, 10)` literal (still
truncates a bound with more than ten decimals — trades an exact rule for a wider
threshold), and 422-ing an over-precise bound (a filter refusing to filter).

**Both sides of a shared filter builder move together or not at all** — the list
and its `/counts` rollup filtering differently is precisely the drift the shared
`_*_list_filters` builders exist to prevent, and there is a test that fails when
only one side is converted.

The guard is two independent scans, each with a negative control: an OpenAPI scan
over the mounted app (a `Decimal` parameter renders `anyOf[number, string]` and the
string branch is what parses exactly; a `float` renders `number` alone) **and** an
AST source scan over `app/api/`, because the shared *private* builders appear in no
OpenAPI schema at all — and those are where the bound actually meets the column.
Both assert discovery non-emptiness, per the `_KNOWN_ROLLUP_COUNT` lesson.

The same rounding reaches a JSON **body** differently, and needs a different fix:
pydantic parses a JSON number into `Decimal('100')` from `100.00000000000000001`,
so only the string form round-trips. The shared parse lives on
`backend/app/schemas/money.py` (`ExactMoneyInput` / `OptionalExactMoneyInput`) —
it accepts an exact decimal string or a JSON integer and refuses a float — and
backs `POST /api/discounts/optimize` (with the frontend caller moved in the same
change, a wire-contract change rather than a retype) and the cash-flow copilot's
plan tools and plan bodies.

The copilot case adds one rung the endpoint case does not have. Its budget arrives
as an **LLM-produced tool argument**, and `ToolSpec.anthropic_spec` derives each
tool's `input_schema` from `model_json_schema()` — so a bare `Decimal` advertised
`number`, instructing the model to send the exact shape the validator refuses. The
annotation now carries a `WithJsonSchema` declaring `string`: the refusal is the
backstop, the advertised schema is the fix. It matters beyond tidiness because
`propose_payment_plan` hashes `str(cash_budget)` into `plan_id` and
`POST /plans/{plan_id}/draft-run` stages a real `PaymentRun` from the plan that id
certifies — so a rounded budget is two wrongs: a different selection, and an id
asserting the rounded figure is what was approved. For the same reason the parse
covers `min_balance_threshold` (also in the `plan_id` preimage) and
`opening_balance` (persisted money), not the budget alone — hardening one leg
leaves the hash half-exact. Neither side normalises the value, because the
preimage is `str()` and rescaling `"8.00"` to `"8"` anywhere would fail a plan's
own stale-plan check without its parameters having changed.

---

## 66. A unique index is not a substitute for the two-phase re-check

**Decided:** 2026-09-04 · `backend/app/services/recurring_invoices.py`

`recurring_invoices` was left without step 2 of the sweep locking shape on the
reading that `uq_invoice_recurring_period` already made the sweep idempotent. It
does not. The index forbids a *second* invoice for a period; the unguarded failure
mode is a *first* invoice for a period that is not due yet, on a distinct period key
the index accepts — replica A generates P and advances the cursor to P+1, replica B
locks, reads the fresh P+1 and generates it early, and the cursor jumps to P+2 so
the real P+1 tick raises nothing at all.

The general rule the two facts add up to: **a uniqueness constraint bounds what a
write may contain; only re-reading the predicate under the lock bounds whether the
write should happen.**

Rejected: widening the index (there is no key that expresses "not due yet"), and a
tenant-level advisory lock (it reintroduces exactly the whole-tenant hold
`background-sweeps.md` § Locking exists to remove).

A related correction in the same change: `ORDER BY next_run_on` is a **partial**
order, so templates sharing a due date can be locked in opposite orders by two
replicas and deadlock. "Ordering by id gives every replica the same lock order"
only holds while the sort key is unique.

---

## 67. An over-receipt is flagged beside `MatchResult.status`, never through it

**Decided:** 2026-09-04 · `backend/app/services/po_matching.py`, `backend/app/services/invoice_warnings.py`

The 3-way leg now excludes cancelled goods receipts (a **denylist** — `status` is a
free-form `String(30)`, so an allowlist would have silently stopped counting
`partially_received`) and flags `received > ordered`. That flag is an additive field
plus an `issues` line; `status` keeps its four existing values.

`mismatch` is owned by the AMOUNT control — `invoice_warnings._refresh_po_match`
composes its message purely from `amount_variance` / `amount_variance_pct` — so
routing a quantity over-receipt through it would emit a message about an amount
variance that isn't there, and `partial` means the opposite thing. An over-receipt
with an in-tolerance amount is a receiving discrepancy, not a billing one.

It reaches the exception queue by a different route: `_refresh_po_match` raises it
**independently of `status`**, the way the 4-way inspection block already does. That
independence is the whole point — `status` belongs to the amount control, so an
over-receipt on an otherwise-`matched` invoice was the case that disappeared
entirely. Severity is `warning`, not the `info` a partial receipt gets: a short
delivery is routinely benign, quantities nobody ordered are not.

Rejected: a fifth `status` value (breaks the persisted `invoice.po_match` contract
every downstream reader and the frontend `PoMatch` type read); reusing `mismatch`
(wrong message, and it would block payment on a receiving-side clerical error); and
a new exception TYPE (`EXCEPTION_TYPES` is a fixed vocabulary with a label-coverage
AST guard, and an over-receipt genuinely *is* an invoice-vs-PO discrepancy).

---

## 68. A tally may need a column its list is not selected on — §48 extended

**Decided:** 2026-09-04 · `backend/app/api/vendors.py`

§48 covers a tally that describes a *narrower or wider* population than its list.
The `/vendors/screening` "Payments blocked" KPI was a different shape. It was
counted off the screening review queue, whose predicate is
`screening_status IN ('match','review')`, but `POST /api/vendors/{id}/block` sets
`payments_blocked` and never touches `screening_status`. So the figure was not
merely narrowed — it was **structurally incapable of counting the thing it named**,
at every page size, forever.

The rule §48 states for filters extends to columns: a tally is computed from a query
that asks the tally's own question. Where the tally's axis is orthogonal to the
buckets beside it (`payments_blocked` vs `status`), it rides the **same aggregate**
rather than becoming a second endpoint or a second query — that is what makes "same
filters, same entity scope" true by construction instead of by convention — and it
is reported as its own field, never folded into `total`.

Two consequences worth stating, because both are places the fix could have gone
wrong:

- **The RBAC asymmetry is left visible rather than papered over.**
  `GET /api/vendors/counts` is gated admin / ap_manager / cfo to match its list
  exactly, as §48 requires in both directions; the screening queue also admits
  `ap_clerk`. A clerk therefore gets a 403 and the card renders "Count unavailable"
  rather than falling back to the queue-derived number — that number is the bug, and
  a fallback would reinstate the defect for exactly one role, which is the hardest
  place to notice it.
- **A KPI that cannot honour a filter says so.** The page's search is a client-side
  filter over different columns than the API's `search`, so the card is labelled
  "All vendors, not just this queue" instead of being wired to a search it does not
  describe. Silently ignoring the search was the original sin here; saying which
  population the number covers is the fix.

---

## 69. The WORM trail quarantines a poison row, never an outage

**Decided:** 2026-09-04 · `backend/app/services/audit_log_shipper.py`, `backend/app/services/retention_sweep.py`

Two SOC 2 evidence paths were failing in opposite directions.

**The shipper stopped.** Batches are all-or-nothing and ordered `created_at ASC`, so
one row a sink refused made `adapter.ship` raise on every tick, re-select the
identical oldest-first batch, and block every newer row for that tenant forever. The
sweep-health streak fired correctly; the defect was that the only remedy was manual.
A failed batch is now followed by a **bounded isolation pass**: rows re-ship one at a
time in order, and a row an adapter refuses is re-offered *to that adapter* with its
`details` replaced by a PII-free marker. Row identity is untouched, so the WORM copy
stays an ordered trail, and the complete row remains in the tenant `audit_log`.

The bound is what makes it safe. **If an adapter refuses the marker version too, the
sink is unhealthy rather than the row poisoned** — the pass stops there, everything
from that row on stays unshipped, and the tick fails as before. That caps an outage
at two extra calls per adapter and stops a transient outage stripping the details off
a whole batch. Substitution is per-adapter, since a row CloudWatch refuses may be fine
for the S3 Object Lock copy. A quarantined row is *not* counted as a sweep failure —
the trail moved and nothing was dropped — but the count and one PII-free WARNING per
row (id + exception class, never the payload) are the operator signal.

**The retention sweep would not stop.** Its `retention.archived` manifest was gated on
`archived or overdue_total`, where `overdue_total` counts `audit_log` rows past the
window — and the sweep never deletes an audit row (migration 0022's BEFORE-DELETE
trigger forbids it, and WORM evidence must not be destroyed anyway). So once a
tenant's oldest audit row crossed its window the condition was permanently true, and
a manifest reading `invoices_archived: 0` was appended on every tick forever — each
one itself an `audit_log` row that later ages past the window and inflates the next
tick's count. Unbounded growth in an append-only, undeletable table.

The gate is now `archived or overdue_unshipped`. Rejected: change-detection against
the previously recorded counts, which needs an extra per-tenant read of `audit_log`
every tick to reproduce a property this gate has for free — `overdue_unshipped` is the
actionable half of the same observation, it cannot inflate itself (a manifest written
now is far younger than the window), and it returns to zero once the shipper catches
up, at which point the manifest stops on its own. `audit_rows_overdue` is still
*reported* in every manifest; it just no longer *causes* one.

The pair is the general rule: **an evidence trail must keep moving past one bad row,
and must stop writing when it has nothing to say.**

---

## 70. The login-failure audit row is written off the response path

**Decided:** 2026-09-04 · `backend/app/api/auth.py`, `backend/app/api/portal_auth.py`, `backend/app/services/audit_dispatch.py`

`/api/auth/login` equalised the bcrypt cost with `dummy_verify()` on the
unknown-address path, then reintroduced the same oracle one line later: a
**known** address additionally `await`ed `dispatch_auth_audit`, which resolves
the tenant DB from the control plane, opens a session, and commits an
`auth.login.failure` row. An unknown address has no org, so it skipped all of
it. Two rejections differing by a whole DB round trip:

```
KNOWN  : [check_rate_limit, check_auth_failures, password_hash_cost, record_auth_failure, dispatch_auth_audit]
UNKNOWN: [check_rate_limit, check_auth_failures, password_hash_cost, record_auth_failure]
```

The supplier portal carried the same defect and **worse**: that request already
holds a tenant session from `get_tenant_db`, but the audit helper opens its own
*second* one to commit through. A third timing signature existed there too — a
legacy vendor-user row with no `organization_id` returned from *inside* the
helper, after the `await` had been entered, so it was neither the fast path nor
the slow one.

Both obvious fixes were unavailable: dropping the audit is worse, and padding
the fast path is masking (guard rail 4). So the write moved **off the response
path**, and both rejections were collapsed onto one shared tail — `_reject_login`
/ `_reject_portal_login` — so they are identical *by construction* rather than
by inspection: one awaited `record_auth_failure`, one non-awaited
`queue_auth_audit`, one 401. `organization_id=None` is **passed**, not branched
on, so an unknown address reaches the same call site.

**`post_commit` is the wrong mechanism here, and that is the load-bearing
detail.** It fires from SQLAlchemy's `after_commit`; a failed login raises
`HTTPException`, so `get_control_db` rolls back and `after_rollback` drains and
discards the queue *by design*. A row queued there would silently never be
written. The trigger is `loop.create_task` — the same spawn mode `post_commit`
itself uses — with a strong reference held so a running task cannot be collected
mid-await.

The guarantee is therefore weaker on purpose, and is stated rather than hidden:
"written shortly after the response, **or reported at ERROR**". ERROR, not
WARNING, because an auth audit row is SOX evidence and nothing retries it. A
bounded 5-second drain runs in the lifespan's `finally`, **before**
`dispose_all_engines()` — the queued write commits through the tenant engine
that call disposes, so the reverse order would silently break every in-flight
row. Bounded rather than unbounded because one hung write would otherwise block
shutdown until an orchestrator SIGKILLs the process, losing every *other* queued
row too; on expiry the abandoned **count** is logged PII-free.

The rate-limit ordering is untouched: `check_auth_failures` still runs on the
SHA-256 of the *submitted* identifier **before** the DB lookup, which is what
makes an unknown address throttle identically.

Only *failure* rows moved. `auth.login.success`, `auth.mfa.challenge_issued` and
the SSO-only refusal all sit after a correct password, so none is an existence
oracle; they still await.

---

## 71. Every approval threshold is denominated in the reporting currency, and they share one value

**Decided:** 2026-09-04 · `backend/app/services/approval_chain.py`, `review.py`, `extraction.py`

`auto_approve_below`, `require_cfo_above`, `max_invoice_amount` and the approval
matrix's per-level amount bands were bare JSONB numbers that never declared a
currency, compared against raw `Invoice.amount` in whatever currency the invoice
was billed in.

Measured against the pre-fix code: a **GBP 9,000 invoice — USD 11,403 at the
rate locked on its own row — was approved by an `ap_manager` with no CFO
signature** under a USD 10,000 `require_cfo_above`, and `resolve_applicable_levels`
routed it to the manager tier of a chain whose senior level starts at 10,000.
Separately, three spotless JPY 100,000 vendors (≈USD 650) pushed the
auto-approve recommendation toward the USD 25,000 cap, so a ¥1,000,000 invoice
read as "below 5,000" and auto-approved unattended.

The denomination is the org's **reporting currency** — not a new convention, the
one `payment_controls.cfo_approval_decision` and `expense_approval.cfo_threshold`
already use. Both sides of every comparison convert through
`currency_conversion.reporting_amount_at_locked_rate` at the rate already on the
invoice row, never one fetched at read time (which would make the gate
non-deterministic).

**Converting one side alone is worse than converting neither**, so all five
sites moved in one commit. They now share a *value* — `approval_chain.GateAmount`,
carrying the figure in the gate currency **and** whether it could be established
at all — rather than a shared spelling. A plain `Decimal` still means "already in
the gate currency". The distinction matters because a convention is what the
sixth comparison forgets; an AST drift guard fails any gate site that reverts to
a raw amount.

**Fail-closed is a different direction at each site, all pointing at a human:**
the gates **fire**; the auto-approve floor does **not** fire; and the chain
**skips its amount bands** so every routing-matched level applies. That last one
is the trap — an empty chain result is *no chain requirement at all*, so
filtering on unpriceable figures could drop the senior level, which is the silent
version of skipping the CFO.

The adaptive **anomaly baseline** made the opposite call deliberately: it
**abstains** (emits `amount_comparison_unavailable`, still runs the approver and
timing rules) because it is advisory and writes no warning or Exception row —
there is nothing to fail closed *on*. `detect_invoice_anomaly`'s `amount` is now
a required keyword-only argument with no default, so nothing can forget it.

The UI followed in the same round: an admin typing `10000` into "Require CFO
approval above" had no way to see which currency that was. Before the
denomination was settled the number was merely ambiguous; afterwards it had a
definite meaning the operator could not see, which is worse. Labels and hints
now name the code, resolved at runtime from the org's reporting currency.

---

## 72. GL code uniqueness needs two partial indexes, and a dirty tenant fails loudly

**Decided:** 2026-09-04 · `backend/alembic/versions/0088_gl_account_code_unique.py`

`gl_accounts.entity_id` is multi-entity's deliberate exception: NULL means the
account is **shared** across every entity, not "unstamped legacy row" as it does
on invoices and vendors. NULLs never compare equal in a unique index, so the
obvious `UNIQUE (organization_id, entity_id, code)` would enforce nothing at all
on the shared chart — the exact place a duplicate is most damaging, since a
shared account sits in every entity's effective chart.

Splitting on the NULL-ness states the two rules separately and enforces both:
`(organization_id, code) WHERE entity_id IS NULL` plus
`(organization_id, entity_id, code) WHERE entity_id IS NOT NULL`. Rejected: one
`UNIQUE (organization_id, code)` across the tenant, which would forbid two
subsidiaries each running the standard `6000` — normal practice, and it would
fail against existing data.

The API guard is deliberately **broader** than the index: create refuses a code
already visible in the caller's *effective* chart (shared ∪ selected entity),
because that is the list a clerk codes an invoice from, whereas an index can only
speak per-chart. The ERP sync upsert was matching on `(code, organization_id)`
with no entity filter, so a sync run under entity B updated entity A's row rather
than creating B's — contradicting the route's own docstring.

**A tenant with pre-existing duplicates fails the migration loudly and changes
nothing**, naming up to 20 offending groups. Contrast migration 0081, which
auto-repairs over-claimed bank transactions because clearing a claim back to
"unmatched" is visibly conservative. There is no equivalent here: duplicate GL
rows differ in `name` / `account_type` / `is_active`, invoices already reference
the code as free text, and picking a survivor silently discards chart
configuration or re-labels spend booked under the other row. GL codes are org
configuration, not PII, so naming them in the error is safe.

---

## 73. Touchless rate means "passed review", not "reached a terminal state"

**Decided:** 2026-09-04 · `backend/app/services/analytics.py`, `backend/app/api/dashboard.py`

The straight-through-processing rate counted `done` — and `paid` — as having
cleared review on status alone. Both are reachable *around* review: `new → done`
is a legal `VALID_TRANSITIONS` edge that skips approval outright, and the Day-0
CSV importer (`services/csv_import`) plants historical rows straight at `done`,
its default, or `paid`, with the workflow engine never running. So a tenant that
migrated ten thousand historical invoices on day one reported near-100%
automation — inflating hardest for the tenant with the least automation to show.

The code had already solved this once and not generalised it: the `failed` leg
counted an invoice as cleared only with the durable `Invoice.approval_date`
stamp, and an unstamped one sat in neither leg. `done` and `paid` are the same
shape of problem, so they now sit in a named
`TOUCHLESS_REVIEW_EVIDENCE_STATUSES` beside it.
`approved`/`sending_to_erp`/`sent_to_erp`/`posted_in_erp`/`payment_scheduled`
stay proof-by-status because every edge into them originates at `approved`, and
every writer of `approved` stamps `approval_date`.

The alternative reading — "reached a terminal state without human touch" — was
rejected because the figure is quoted to leadership as evidence the platform is
doing the approving, and an invoice that never entered review is not evidence of
that. An unevidenced invoice leaves the **denominator** too, rather than being
parked in the bounced leg: counting it as "finished review and did not clear"
would deflate the rate exactly as dishonestly as the old rule inflated it.

`compute_touchless_rate`'s optional kwargs collapse into one **required**
`review_cleared_count`, so an un-updated caller raises `TypeError` rather than
quietly re-publishing the old wider number. This moves a previously reported
figure **downward** at deploy time for any tenant using the `new → done`
shortcut or the importer; `backend/docs/analytics.md` records that so a
dashboard delta is not misread as an automation regression.

---

## 74. Two routing numbers on a vendor, and why the checksum left Pydantic

**Decided:** 2026-09-04 · `backend/app/schemas/vendor.py`, `backend/app/api/vendors.py`, `backend/app/api/portal.py`, `backend/app/services/payment_adapters/base.py`

Larger US banks publish a different ABA for incoming Fedwires than for ACH, so
one generic `routing_number` could not express a payable wire at those banks.
Rather than reinterpret stored data, `routing_number` keeps its existing meaning
(ACH) and `wire_routing_number` is added beside it — no backfill, no migration,
`bank_details` is JSONB. `payment_adapters/base.resolve_routing_number` is the
single resolver: wire rails prefer the wire ABA and fall back to the ACH one (a
bank publishing one number uses it for both), while ACH rails **never** borrow
the wire number, because a bank with two ABAs rejects an ACH file addressed to
its Fedwire number — borrowing would convert a missing-data problem into a
returned item at the vendor's expense. The new field travels the existing
dual-control staging path, so it is not a BEC bypass.

The checksum check was deliberately moved **out** of the field validator.
FastAPI renders a Pydantic `ValidationError` as a 422 whose body echoes the
rejected `input`, and that input is the whole `bank_details` dict including the
account number — so "your routing number has a typo" was answering with banking
data, against the invariant that PII/banking data stays out of error bodies. It
now runs at the two chokepoints instead: `_stage_ap_bank_change` (which all
three AP entry points share) and `approve_change_request` (where any staged
change, AP- or portal-submitted, is applied), raising an `HTTPException` naming
only the field.

The supplier portal had the identical validator and kept the identical leak, so
it moved the same way — and sharing one helper closed a second gap in passing:
the portal validator checked only `routing_number`, so a malformed wire ABA
reached staging unvalidated and failed only at approve.

---

## 75. Re-extracting a resubmitted supplier invoice needs two guards, not one

**Decided:** 2026-09-04 · `backend/app/api/portal.py`, `backend/app/services/extraction.py`, `backend/app/services/extraction_dispatch.py`, `backend/app/services/extraction_lambda.py`

`POST /portal/invoices/{id}/resubmit` shipped without re-extraction because a
fresh pass calls `vendor_matching.match_and_link_vendor` and can re-link
`Invoice.vendor_id` to a different supplier — dropping the invoice out of the
`vendor_id ==`-scoped portal list, so the vendor loses sight of their own
resubmission. The scoped fix was a `skip_vendor_match` flag.

The code needed a second one. Re-extraction also re-enters
`decide_auto_approve`, so a tenant with unattended approval configured would let
a supplier launder a human-rejected invoice past the reviewer who rejected it:
submit garbage, get rejected, resubmit a doctored PDF that extracts under the
auto-approve floor. `suppress_auto_approve` is therefore not an extra — it is
what makes re-extraction safe to enable at all.

Both default `False` and travel together in one frozen `ExtractionOptions`, on
the local queue tuple's fourth slot and as flat SQS keys, so the tuple's shape
stops changing, a legacy 3-tuple still drains, and an absent key decodes to
today's behaviour. `lambda` mode reads them too: carrying the flags without
honouring them would have left the hole open in exactly one dispatch mode, which
is worse than not shipping the flags.

`skip_vendor_match` pins `vendor_name` as well as `vendor_id`, because
`PATCH /api/invoices/{id}` re-resolves a stale link from the name — pinning only
the id defers the re-link to the reviewer's next save rather than preventing it.
`rejected → pending` is not in `VALID_TRANSITIONS`, so the route takes the
documented `rejected → new → pending` rework loop rather than widening the state
machine from an API handler; dispatching from `new` was rejected because
`new → failed` is illegal and extraction's own error handler would raise inside
its `except`.

---

## 76. Per-box 1099 allocation follows the GL account, and reconciles by construction

**Decided:** 2026-09-04 · `backend/app/services/tax_1099.py`, `backend/app/services/tax_filing_adapters/base.py`

A vendor's whole reportable YTD total was filed in one box, which is wrong for
any vendor whose spend spans categories. The box for each payment is now
resolved from `Invoice.gl_account` through a per-org mapping on
`Organization.settings.tax.boxes` (exact + `prefix*` GL rules, per-vendor
overrides, a named `fallback_box`) — the coding AP already does, rather than a
new field nobody would fill in. The per-vendor override lives in that settings
blob rather than on the `Vendor` row: there is no general-purpose vendor JSON
column, and borrowing `bank_details` / `risk_factors` to carry a tax setting is
how columns rot.

**No proration.** Each payment lands whole in exactly one box, so the per-box
`Decimal`s sum to the reportable total to the cent with no rounding step and no
residual to lose. Splitting a vendor total by ratio needs largest-remainder
correction, and a correction step is exactly the quiet-gap class this feature
exists to remove. Spend no rule matches is not dropped: it goes to the named
fallback (default `NEC-1`, so an unconfigured tenant behaves exactly as before)
and is separately surfaced as `unmapped_paid`, alongside a published
`box_unallocated` residual and a `box_allocation_reconciled` flag — a
reconciliation guarantee nobody can read is one nobody can check. A configured
box code outside `BOX_CATALOG` is dropped rather than resolved to some box, so
the affected money shows up as unmapped instead of silently filing in the wrong
place.

`FilingFormPayload` carries the split (`box_amounts`) alongside the form total,
because a 1099-MISC with rent *and* medical payments is two boxes on one form
and transmitting only the total files the whole figure in whichever box the
partner defaults to. It is empty when there is no split to send, and a consumer
then files `box_amount` against its own box of record — the pre-allocation
behaviour.

---

## 77. Invoice-number normalization widens a match, so the scope is what keeps it safe

**Decided:** 2026-09-04 · `backend/app/services/invoice_warnings.py`

The always-on duplicate gate compared `lower(trim(invoice_number))`, so one
supplier's `INV-001` and `INV-1` were two distinct payables. The
semantic-similarity pass was the only backstop and it is inert whenever RAG is
off — the common configuration — so the same invoice could be paid twice.

Normalization collapses leading zeros inside each digit run and treats separator
*runs* as interchangeable noise. It collapses them to a single `-` rather than
deleting them, because deleting would make `INV-1-2` and `INV-12` the same
string; and it never strips non-digits, because reducing `INV-1` and `PO-1` to a
bare `1` is guessing rather than normalizing. Only leading zeros go, so `INV-100`
can never collide with `INV-1`.

Because normalization only ever *widens* a match, the candidate query keeps
precisely the scope the exact check already had — same tenant session, same
vendor by stable `vendor_id` or case-folded name, `id != self`. The scope is the
safety, not the string rule. With no migration available, the comparison is a
bounded in-Python narrowing pass rather than a query expression: SQL prefilters
on the ASCII letter skeleton (a superset invariant, so it can cost a match but
never invent one) and caps at 500 rows newest-first, and the pass runs only when
the exact match has already missed — so existing behaviour and the common-path
cost are unchanged. The accepted limit is invoice numbering where a leading zero
is significant within one supplier.

---

## 78. The last two sweeps page rather than cap, and a lead window belongs in SQL

**Decided:** 2026-09-04 · `backend/app/services/discount_auto_trigger.py`, `backend/app/services/contract_renewal.py`

Both sweeps loaded a tenant's whole candidate set in one unbounded `SELECT`. A
`LIMIT` was unavailable to either, for the reason `background-sweeps.md`
§ Locking already states: an offer skipped for a below-threshold ROI stays
`offered`, and a contract outside its lead window stays un-alerted, so neither
removes itself from the candidate set and a capped tick would re-serve the same
lowest-id rows forever — the exact starvation `approval_escalation` was rewritten
to avoid. Both now keyset-paginate until the tenant is exhausted; the per-item
`FOR UPDATE` re-check each already performed is what makes a page boundary safe.

`contract_renewal`'s `end_date <= today + 3650 days` pre-filter is replaced by
the real per-row window in SQL, `end_date - CAST(:today AS DATE) <=
COALESCE(renewal_notice_days, <default>)`. The old predicate matched effectively
every active contract with an end date, so the "keeps the fetched set small" it
claimed was false for any tenant whose contracts are not all decades out — it was
the whole-candidate-set load wearing a `WHERE` clause. The explicit
`CAST(:today AS DATE)` is load-bearing rather than cosmetic: a bare bind
parameter leaves Postgres choosing between `date - integer`, `date - date` and
`date - interval`, three overloads with three different result types. Because a
per-row interval expression is precisely where SQL and Python coercion diverge,
both halves resolve the NULL fallback through one `resolve_notice_days` helper
and a test evaluates the real expression in real Postgres against every boundary
and the NULL case — which also gave `FEOH_CONTRACT_RENEWAL_DEFAULT_NOTICE_DAYS`
its first reader, having been declared and documented but consumed by nothing.

---

## 79. Budget spend legs count what they refuse, and the rollup discloses it

**Decided:** 2026-09-04 · `backend/app/services/budget_service.py`, `backend/app/api/budgets.py`

Every leg of `compute_budget_spend` is scoped to the budget's own currency
because the legs never convert — summing unlike face values would be worse than
excluding the row. That was right, but the excluded rows were dropped
**silently**, so `committed` / `actual` read exactly like complete figures
whether or not a foreign-currency requisition or invoice had been left out.

Each leg now returns its total *and* its excluded count, computed in one query
via a Postgres `FILTER` clause so the count costs no extra round trip. A NULL
currency counts as excluded — `(currency = 'X') IS NOT TRUE`, not `<> 'X'`,
which would swallow it from both sides. The count rides onto both
`GET /budgets/{id}/spend` and the new `GET /budgets/rollup`, and `/cfo` renders
it as a `role="alert"` line naming the figures as a floor, the same treatment
the cash-position card gives its unconverted outflows.

Rejected: converting on read (an FX rate fetched on a read makes the figure
non-deterministic) and a whole-set rollup total across currencies (denominated
in nothing real). The rollup is whole-set by design — a paged rollup presented
as an org-wide total is the exact dishonesty being avoided.

---

## 80. An exact multiply, and why a money preview refuses rather than repairs

**Decided:** 2026-09-04 · `frontend/src/lib/utils/money.ts`

`sumMoney` added exactly, but nothing multiplied exactly, and two previews scale
money by a non-money factor — a requisition line's `quantity * unit_price` and a
discount tier's `base_amount * percent / 100`. Those were the last three
`number`-typed money fields on the frontend ratchet, and they were blocked on
the missing primitive rather than on judgment.

`scaleMoney` parses both operands as plain decimals, multiplies as `BigInt`s at
their combined scale, and rounds HALF_UP to the target scale in **one** step —
never two, which is where a half-cent goes missing (`1.004 * 1.004` rounded
twice gives `1.00`; the exact product is `1.008016`, i.e. `1.01`). `divideBy`
folds a constant divisor into the same step so a percentage needs no lossy
`percent / 100` first.

It returns `null` for input it cannot read, and callers render a dash. This is
the same rule the round applied to seven forms that were `parseFloat`-ing money
to the wire: a preview that silently repairs unreadable input is how a wrong
figure reaches a field the user then trusts, and the sharpest instance was the
approval-gate thresholds — `require_cfo_above` sent as `null` when the text
didn't parse, silently removing the CFO gate.

---

## 81. Imported invoices leave the touchless rate by provenance, not status

**Decided:** 2026-09-05 · `backend/app/services/csv_import.py`, `backend/app/services/analytics.py`, `backend/app/api/dashboard.py`

§73 narrowed the touchless NUMERATOR to require positive evidence of review.
The DENOMINATOR had the mirror of that hole: a CSV-imported `rejected` row sat
in the bounced leg as though a reviewer *here* had sent it back, deflating the
rate exactly as imported `done` rows used to inflate it.

Evidence cannot close it — nothing ever writes an approval stamp on a
rejection, so gating that leg would zero the bounced population outright rather
than exclude the imports. Neither can status: `done`, `paid` and `rejected` are
each reachable both by import and natively, so any status rule guesses. So the
importer records the fact directly: `Invoice.meta["imported"] = {"at",
"source"}`, one reserved key on an existing JSONB column, no migration.
`compute_touchless_rate` subtracts marked rows from every leg via a **required**
`imported_pipeline` kwarg — the §73 precedent, so an un-updated caller raises
`TypeError` instead of quietly publishing a padded denominator.

The NULL-jsonb trap is load-bearing rather than incidental. Postgres' `?`
operator returns NULL, not false, on a SQL-NULL `meta`, so without an
`IS NOT NULL` guard every meta-less legacy row silently left the numerator while
staying in the denominator — 33.3% against a true 50.0% in the regression test.
`native_invoice_clause` is written as the exact complement of
`imported_invoice_clause` for that reason, and both live with the marker's
writer so the reader cannot drift from it.

An UNMARKED row is treated as native, because absence of the marker means "we do
not know" and it is only ever written going forward. There is deliberately **no
backfill**: stamping a historical row on an inference is precisely the guessing
this replaces. Rows imported before the marker shipped therefore stay in the
population, which `backend/docs/analytics.md` states rather than papers over.
The marker also covers the CSV importer only — any future bulk path must stamp
it with its own `source` or its rows read as native, and no guard can enforce
that on code that does not exist yet.

---

## 82. The budget rollup is the per-budget spend query, widened

**Decided:** 2026-09-05 · `backend/app/services/budget_service.py`

`GET /api/budgets/rollup` is whole-set by design, so its cost had to stop
scaling with the budget count: 600 queries / 297 ms at 200 budgets, now 6 /
8.6 ms, with `GET /budgets/{id}/spend` unchanged at 3.

The obvious fix — a second, grouped SQL shape for the rollup — was rejected.
Both endpoints publish an `excluded_row_count` disclosure telling the reader the
money figures are a floor (§79), and **a disclosure the org-wide view and the
per-budget view can disagree about is worse than none.** Instead the grouped
query became the *only* implementation and `compute_budget_spend` is it,
narrowed to one budget. The currency rule is written once against
`Budget.currency` as a **column** rather than a Python literal; that
substitution is the whole trick, because the same predicate then answers for one
budget or a thousand.

Correlating the entity / period / dimension conditions cost the planner its
index and regressed the *single-budget* invoice leg from 0.6 ms to 10.4 ms over
40k invoices — on the path `GET /budgets/check` sits in before every requisition
submit. So each is restated once more as a set-level narrowing predicate,
logically redundant and provably implied by conditions already in the query, and
derived from the same budget set — which restores the identical index scan at
both scopes. That is one query shape, not a fork. The invoice leg batches by
dimension rather than folding four columns into one `CASE`, which would have
bought one query and cost every index.

The standing guard is `test_rollup_agrees_exactly_with_every_per_budget_spend`,
which folds every per-budget response by currency and compares figure for
figure including `excluded_row_count`. It was mutation-tested: forking
`compute_budget_spend` to under-report by one makes it fail, so the guard is not
vacuous.

---

## 83. Two tabs, two search keys

**Decided:** 2026-09-05 · `backend/app/api/bank_reconciliation.py`, `frontend/src/routes/bank-reconciliation/+page.svelte`

`/bank-reconciliation` has two tabs backed by two different endpoints:
Outstanding queries `/outstanding` over vendor names, invoice numbers, payment
methods and bank-line references; Statements queries the paginated statement
list over account identifiers, source formats and ISO period dates. A single
`?search=` would be carried from one to the other on every tab switch, silently
applying an account term to a vendor filter and rendering an empty tab the user
never asked for. The Statements term therefore lives on `?statement_search=`,
with its own debounce, its own applied-term guard and its own entry in the
page's single `syncUrl()` writer. Clearing a shared term on tab switch was
considered and rejected: it discards a filter the user set deliberately and
makes the URL non-restorable for one of the two tabs.

Both legs are server-side, and both are folded into the same filter object the
endpoint's own `COUNT` reads, so a narrowed table can never be headed by a
whole-set count (§48). Period dates match as ISO via `to_char`, **not**
`CAST(... AS text)`, which resolves against the session `DateStyle`: the row
renders a *localised* date, and matching that in SQL would make the result set
depend on the caller's browser language. `currency` is deliberately excluded
from the searched columns — it is never shown on that tab, and a three-letter
code is the highest-noise substring in the set.

---

## 84. Forecast variance renders "not computable", not `0%`

**Decided:** 2026-09-05 · `frontend/src/routes/cfo/forecastVarianceSummary.ts`

`POST /api/analytics/forecast_variance` emits `variance_pct = 0` whenever the
forecast is not positive, since a percentage of zero has no value. On a CFO's
screen `0%` reads as "we landed exactly on plan" — the most reassuring statement
available, over the one row carrying no information at all. That is the same
failure §34 records for the fraud-rate trend and §79 for the budget rollup's
utilization, and both fixed it at the API by emitting `null`.

This surface fixed it client-side instead: `variance_pct` is a shipped field
typed `number`, and this panel is its only consumer, so the honest render costs
nothing while the wire change would need its own migration of every reader. If a
later slice unifies the three, the API is the right place — the client guard is
then redundant, not wrong.

The entry form follows the round's other rule about money a user types: raw
decimal text, validated once at submit, **refused** with a toast. A month typed
without a readable amount refuses the whole submit rather than sending `0`,
which would make the variance equal the entire actual outflow and report a fake
0% — the same reassuring-wrong-answer this entry exists to prevent.

---

## 85. An unused dependency was setting the runtime the project builds on

**Decided:** 2026-09-05 · `frontend/package.json`, `.github/workflows/`, `deploy/deploy.sh`

`isomorphic-dompurify` declared `engines: ^22.22.2 || ^24.15.0 || >=26.0.0`
while every CI job ran Node 20. pnpm does not enforce `engines` without
`engine-strict`, so it installed without complaint — which is precisely why it
drifted unnoticed. The follow-up proposed bumping it to `^4.1.0`, the
maintainer's own "same code under honest semver".

It was **removed** instead. Nothing had ever imported it: the XSS defence in
this tree is that no component uses `{@html}` at all, and every chat / assistant
/ invoice bubble binds plain text and says so in a comment. A dependency with
zero call sites was dictating the runtime the whole project builds on and
dragging `jsdom` into the graph. The floor still exists, but it now belongs to
`jsdom` as **vitest's** test-environment peer — something the project actually
uses. If a case for user-supplied markup ever arrives, the sanitizer comes back
*with* its call site in the same change, never ahead of one.

Node moved to 24 (Active LTS to 2028-04-30; 20 reached end-of-life 2026-04-30)
at **nine** `setup-node` sites across six workflows — not the four the follow-up
named — plus `deploy/deploy.sh`, which builds the *production* frontend in a
`node:*-alpine` container. Both halves move together: a deploy image behind the
CI pin means production builds on a runtime CI never tested, and that
disagreement is invisible until it breaks. Separately, the `# vN.N.N` comment
beside `setup-node`'s SHA pin read `v6.0.0` at eight of nine sites against a SHA
that is really `v7.0.0`; Scorecard reads the SHA, so nothing caught it.

---

## 86. A vanity host is a hostname the SPA must recognise, not a slug it can parse

**Decided:** 2026-09-05 · `frontend/src/lib/hostRouting.ts`, `frontend/src/lib/tenant.ts`, `frontend/src/routes/+layout.svelte`, `frontend/src/routes/portal/+layout.svelte`

White-label custom domains had shipped, were documented, had an admin panel and
a provisioning runbook — and could not work. `backend/app/tenant.py` maps an
inbound `Host` to a tenant, but only when `X-Tenant-Slug` is **absent**, and the
SPA's rule was "the first label of any 3+-label hostname". So `ap.acmecorp.com`
pointed at tenant `acme` sent `X-Tenant-Slug: ap` and every call 404'd
`Unknown tenant: ap`. A bare apex sent no header but still called the
*build-time* `PUBLIC_API_URL` origin, so the backend never saw the vanity `Host`
either. The feature was unreachable in both shipped deployment shapes, and the
`/organization` panel's own placeholder was the broken case.

Three calls worth recording.

**The fix is classification, not validation.** The cheap option on the table was
to reject any custom domain whose first label isn't the tenant slug, forcing
`acme.acmecorp.com`. That makes the panel honest but keeps the product narrower
than what customers buy a vanity domain for. Instead the SPA now classifies a
hostname against an operator-declared `PUBLIC_PLATFORM_DOMAINS`: a platform
subdomain yields a slug, and **anything else deliberately yields none**, so the
`Host` fallback the backend already had is finally reachable. Sending a guessed
slug is strictly worse than sending nothing, because the header is what
suppresses the lookup.

**Unset config replays the old rule exactly.** The tempting default for an empty
list is "then no host is a platform host" — which would have made every existing
static build stop sending `X-Tenant-Slug` on upgrade and fall back to a `Host`
map with no entries. Total breakage, in exchange for a feature nobody had
enabled. The cost of the safe default is that custom domains stay unreachable
until an operator opts in, which both docs state plainly. The var is read
through `$env/dynamic/public`, not `static`, because a static import of an unset
variable is a hard build error and thirteen-plus CI and deploy sites pass only
`PUBLIC_API_URL`.

**Suppressing the header is only half of it.** A vanity host that still calls
the build-time API origin hands the backend the *platform's* `Host`. Only a
same-origin request carries the vanity hostname, so the API base is now resolved
at runtime and collapses to `/api` on a vanity host. That turns "terminate
`/api` on the same origin" into an operator requirement rather than an
implementation detail, which is why it is in the runbook and the panel's own
help text rather than only in code.

The layouts are the part that would have made all of the above still not work:
both gated rendering on `getTenantSlug()`, so a correct `null` on a vanity host
rendered the **marketing landing page** to a paying customer on their own
domain. They now gate on `hasTenantContext()` — "does this host carry a tenant",
which is a different question from "what is its slug", and the only one a render
gate should ask.

---

## 87. A passkey's relying party comes from the account's org, never from the header

**Decided:** 2026-09-05 · `backend/app/services/webauthn_rp.py`, `backend/alembic/versions/0091_webauthn_credential_rp_id.py`

A WebAuthn credential is bound to one registrable domain, and
`FEOH_WEBAUTHN_RP_ID` was a single global — so a tenant on a vanity host had a
strictly reduced second-factor menu. Making the RP ID per-tenant means deriving
it from a hostname, and the hostname arrives in a client-supplied header, so the
whole question is what makes that safe.

The answer is that the `Host` is never *trusted*, only *matched*. The
custom-domain list consulted belongs to the org that owns **the account the
ceremony is for**, resolved through `user.organization_id` — not to a tenant
looked up from the header. A forged host, an unknown host, and another tenant's
genuinely-registered vanity domain are therefore all the same thing here: not on
this account's list, so the platform RP applies. Fail closed, with no branch
where an attacker-supplied string becomes the relying party.

Register and authenticate agreeing is enforced rather than intended: the RP ID
is bound into the single-use Redis challenge and re-checked on finish, so a
ceremony begun on one host and completed on another is refused instead of
persisting a credential bound to a domain the authenticator never signed. A host
already *under* the platform RP ID keeps the platform RP even if separately
registered, so existing passkeys are never stranded.

**The migration story was implemented, not deferred**, because the honest
version of "passkeys now work per-tenant" is that a credential registered on the
platform subdomain genuinely cannot be presented on a vanity host — that is
WebAuthn, not a bug to code around. So it is made legible instead of silent:
`webauthn_credentials.rp_id` records where each credential lives, the list
endpoint reports `usable_here`, and the authenticate and step-up paths name the
host an account's passkeys belong to rather than failing opaquely. That message
is deliberately *not* opaque — the caller has already proved account control and
the hosts named are the tenant's own — while an account with **no** passkey at
all keeps the opaque answer, so the difference can't be used to probe whether a
factor is enrolled.

---

## 88. Two ERP push paths, and the one with the retry was the unreachable one

**Decided:** 2026-09-05 · `backend/app/services/erp.py`

`erp.py` carried `send_to_erp` and `send_to_erp_internal`. Production reached
only the second — `api/workflow.py` transitions the invoice itself and then
dispatches, so `erp_dispatch` and `erp_lambda` both call `_internal`. The two
had diverged on the thing that matters: `send_to_erp` had a 3-attempt
exponential backoff, and the reachable one had **no retry at all**, sending a
transient 503 or timeout straight to `InvoiceStatus.failed`.

The retry semantics were *tested but not shipped*, which is exactly why nobody
noticed: `test_erp_push_flow.py` called it "the load-bearing path",
`test_erp_adapter_idempotency.py` opened by citing its retry loop, and
`retry_erp` reset an `erp_retries` counter nothing in production ever
incremented. Every one of those statements was false. This is the same shape as
the diverged DPO calculation in §31 — two copies of one computation, the
unreachable one carrying the behaviour everyone believed in.

The retry moved to the reachable function and the copy was deleted, rather than
the reverse. Retrying automatically is safe here specifically because `_call_erp`
sends the invoice's `correlation_id` as the adapter's idempotency key, so a
retry after a timeout the ERP actually applied returns the existing document
instead of posting a second vendor bill. The transaction is committed before
each backoff sleep: `erp_dispatch` builds a `pool_size=1` engine per send, and
sleeping with the connection checked out would pin a worker slot for the whole
backoff instead of just the call.

Deleting the twin, rather than keeping it as the "full" entry point, is the
point of the change. A second push path is what allowed the divergence, and
nothing can drift against a function that no longer exists.

---

## 89. Half a validated pair is the worse failure

**Decided:** 2026-09-05 · `backend/app/schemas/vendor.py`

`validate_bank_routing_fields` is the documented chokepoint every bank-detail
write passes through — the AP staging path, the supplier portal, and
`approve_change_request`, where the dual-control BEC sign-off is applied. It
validated `routing_number`, `wire_routing_number` and `sort_code`.
`validate_uk_account_number` existed in `utils/banking.py`, was tested, and was
reached by nothing.

A UK payee is identified by the sort code **and** the account number together.
Checking one half meant a valid sort code alongside a five-digit account number
cleared staging, cleared the second approver, and surfaced days later as a
returned or misdirected payment — precisely the outcome the module's own
docstring says it exists to prevent. The asymmetry was invisible because the
sort-code half *was* checked, so the payload looked validated.

The account number is validated exactly when a sort code is present, and
deliberately not otherwise: `account_number` is the generic key every rail uses,
and a US or IBAN payee's is not eight digits. Validating it unconditionally
would refuse most real payees — the failure mode this gate must never have.

---

## 90. Conformance rules are asserted only where we hold the whole list

**Decided:** 2026-09-05 · `backend/app/services/e_invoice/{en16931_rules,codelists}.py`

`bis3_conformance_errors` gates whether we declare BIS Billing 3.0 at all, so a
document that fails it provably does not conform — that asymmetry is what makes
the conditional declaration sound. It checked mandatory elements only, so a
document that *passed* could still fail the real validator on arithmetic.

Implementing the EN 16931 calculation rules immediately earned its keep: the
generator mapped `Invoice.subtotal` into both BT-106 (sum of line nets) and
BT-109 (total without VAT), but `subtotal` is derived before discount and
shipping apply. **Every invoice carrying a discount or a shipping charge went
out contradicting itself three ways** — BR-CO-13, BR-CO-15 and BR-CO-17 — with
our conformance claim stamped on it. Fixed at the source in `mapper.py`, not by
relaxing the rule.

The judgment call is on code lists. Currency, country, VAT category and document
type are enforced as membership, because those lists can be held completely.
Unit of measure, payment means and the EAS scheme get a **shape check only**: a
curated partial list would 422 a genuinely conforming send on a rare-but-valid
code, and since this gate hard-refuses, refusing a conforming document is a
worse failure than the detection gap it would close. That follows the precedent
`tax_rules.py` already set — an unknown country is skipped, never rejected. For
the same reason the lists lean deliberately inclusive: over-inclusion only
weakens detection, under-inclusion refuses real documents.

The official Schematron is still not vendored, so a pass remains "nothing we can
compute objects", not a conformance guarantee. The failure direction is the one
that carries the weight, and it is unchanged.

---

## 91. One tenant-URL resolver, and the two call sites deliberately left out of it

**Decided:** 2026-09-05 · `backend/app/utils/tenant_urls.py`, `backend/app/api/organization.py`

`FEOH_TENANT_URL_TEMPLATE` was one global with `{slug}` substituted inline at
every call site, so a tenant reachable at its own hostname still got approval
links, portal invites and password resets pointing at
`<slug>.<platform-domain>` — working links that undo the white-label the vanity
domain was bought for. The follow-up named six call sites; there were **ten**.
Two of the extra ones are why the count matters: `services/supplier_chat.py` did
its own substitution, and `services/card_issuance.py` did it on `api/payments`'
behalf through an `org_slug` parameter, so neither read as a template call site
from the outside.

The per-org override is a *complete* base URL and the global is slug-shaped by
construction, so `{slug}` is optional in the resolver: substituted when present,
used verbatim when not. One rule, both sources. Rejected: a separate "vanity
host" setting alongside the template, which is two spellings of one question.
Substitution is `.replace`, not `.format` — a template is operator- and
admin-supplied text, and `str.format` raises on any unrelated brace; the chat
link used `.format` inside a bare `except`, so a stray `{` silently dropped the
link from the email.

**An empty result is a real answer.** Every caller now omits the URL line rather
than fabricating one, and the two callers carrying their own hardcoded
`http://{slug}.localhost:7777` dev fallback lost it. `_password_reset_url`
returning nothing skips the email entirely instead of sending a relative
`/login/reset-password?token=…` — a dead link that also burns a live single-use
credential. The endpoint's generic response is unchanged, so this introduces no
enumeration difference.

**SSO stays on the global template on purpose**, and this is the part that is
not laziness. The OIDC `redirect_uri` and the SAML bridge URL are values
*registered with the customer's IdP*. Re-pointing them at a vanity host silently
breaks every SSO login until the operator re-registers them — an
operator-sequenced migration, not a config read. The "convert everything"
reading of the follow-up was rejected, and both exemptions are asserted in the
drift guard so they read as decisions rather than misses.

**The platform domain is derived, not declared.** It comes from
`FEOH_TENANT_URL_TEMPLATE`'s own host with the `{slug}` label stripped, because
that is where the platform's hostname shape already lives; a new env var would
be a second source that can disagree with the first. It is what lets
`PUT /branding/custom-domains` refuse a host under the platform domain — such a
host is already routed by subdomain, so registering it hands the custom-domain
resolver and the subdomain resolver conflicting claims on one name, and lets one
tenant claim a hostname another tenant's slug owns or takes at the next signup.

**Adding a field to `BrandConfig` widens an unauthenticated endpoint.**
`GET /portal/branding` is public-by-design and returns the whole config, so the
new field would have published a staged, not-yet-cut-over vanity hostname to
anyone who asked. It is blanked there explicitly. The general rule this records:
a new `BrandConfig` field needs a publish / don't-publish call, not a default.

---

## 92. Two URL fields, because one of them is registered somewhere we don't control

**Decided:** 2026-09-05 · `backend/app/services/sso.py`, `backend/app/api/{auth_sso,auth_saml,organization}.py`

§91 left the OIDC `redirect_uri` and the SAML bridge URL on the global template
while the other eight call sites moved per-tenant, and named the reason: they
are registered at the customer's IdP. Closing that gap did **not** mean folding
them into `tenant_url_template`. It meant a second field.

`tenant_url_template` is admin-flippable with no external dependency — worst
case an invite link points somewhere odd. `sso_callback_base_url` is half of a
handshake with a system we do not administer: changing it without adding the new
URI at the IdP first breaks every SSO login for that tenant. One field would mean
an admin fixing invite links silently takes SSO down. So they are separate, the
callback is opt-in, unset is byte-for-byte the old behaviour, and the runbook
carries the ordered migration (add at the IdP → verify a real login → set this →
only then remove the old URI).

Two smaller calls under it.

**The `Host` fallback needs no JWT cross-check here**, unlike `get_tenant`. These
entry points are public and pre-authentication, and a forged `Host` can only
select a tenant that *registered that exact hostname* — the same choice `?slug=`
already gave anyone. The state/nonce (OIDC) and RelayState (SAML) are then minted
and consumed against that tenant. An unresolvable host reuses the **existing**
404 verbatim rather than inventing a response, so the change adds no enumeration
surface.

**The wipe hazard is the part that would have bitten.** Unlike `custom_domains`,
which is protected by not being a `BrandConfig` field at all, this one is — so
`model_dump()` emits it as `""` whenever a caller never mentioned it, and the
`/organization` panel PUTs the whole config. An admin editing the product name
would have silently cleared an IdP-registered callback and taken SSO with it,
with nothing on screen suggesting it. The fix is `model_fields_set`: an omitted
value is carried forward, an explicit empty string still clears it (that is the
documented rollback), and the response echoes what was **stored** rather than
what was sent, so a caller is never told the field is empty when it isn't.

---

## 93. `.get(k, {})` and `.get(k) or {}` were not two styles

**Decided:** 2026-09-05 · `backend/app/services/approval_chain.py`

Eight sites read `state_data["approval_levels"]` by hand in two spellings. The
follow-up filed it as ownership debt with "no behavioural bug found today", and
the instruction was to route them all through the existing, uncalled
`get_chain_progress`. Both halves of that turned out to be wrong.

The spellings differ on a stored `null`: `.get(k, {})` returns `None`, and every
consumer immediately calls `.get("levels", …)` on the result. That is an
`AttributeError` on the approval path, not a routing difference. The two sites
using it survived only because their callers happened to test truthiness before
subscripting — reordering one line would have made it live. `or {}` is correct
and is now the owner's behaviour.

And the uncalled helper fitted only three of the eight sites. Two of the others
read the chain out of a **deep copy** of `state_data` and mutate it in place;
the copy is what makes SQLAlchemy's dirty check see the write, so an
instance-shaped reader would have handed back the un-copied object and broken
the persistence. A function written without callers is not automatically the
right abstraction — ownership was reshaped around what the call sites actually
do (a raw-mapping reader, a thin instance front door, a writer, a clearer).

A *truthy* wrong-shaped value (a list, a string) is deliberately passed through
rather than coerced to `{}`. That is corrupt state, and the escalation sweep's
per-instance `try` is built to count it as a failed instance; coercing would
silently drop a real chain's requirement out of an approval path.

---

## 94. A read should not need a write to answer its own question

**Decided:** 2026-09-05 · `backend/app/api/email_intake.py`

`GET /api/organization/email-intake` returned `address: null` for two unrelated
reasons — the platform has no `FEOH_EMAIL_INTAKE_DOMAIN` (the committed default),
or this org has no token yet — and those call for opposite copy: "email intake
isn't available on this deployment" versus "click to create your address". The
payload could not tell them apart, and `enabled` only disambiguated *after* a
token existed, because provisioning is what sets it.

The UI's honest workaround was to offer a non-destructive "create" in the
ambiguous state and only claim the deployment was unconfigured once the server
had proven it — i.e. establish a read-only fact by performing a write. That is
the wrong shape even when the write is harmless. `domain_configured` is an
operator-config boolean, PII-free, no migration, and it answers on the first
read.

The client reads it as `?? true`. An older backend that doesn't send the field
degrades to assume-available, which is the safe direction: reporting a working
deployment as switched off is worse than the pre-existing ambiguity.

---

## 95. The 422 stopped being a string, and the client's translation table went with it

**Decided:** 2026-09-05 · `backend/app/services/e_invoice/validate.py`

`EInvoiceValidationError.__str__` emitted `field: code`, so `FieldError`'s
PII-free `message` ("FatturaPA requires a Partita IVA…") never left the server
and every client had to maintain its own code→prose map. The frontend had one.
It covered 4 codes and 12 field paths out of dozens, so most rows already fell
through to a bare `"was rejected (BR-CO-09)"`.

The body now uses FastAPI's own `[{loc, type, msg}]` validation-error shape,
chosen because the app's shared `formatApiDetail` **already** flattens exactly
that — so no endpoint-specific client handling was needed and `api.ts` did not
have to change. `__str__` is deliberately untouched: two services log it, and
only the HTTP body moved.

The rule id rides on **two** channels — `type`, and folded into `msg` — because
a client that flattens keeps only `loc` and `msg`, and the rule id is precisely
the half a receiving Access Point's own validator will name.

The trade-off, stated rather than hidden: those refusal sentences are now the
server's English instead of localized strings. It is still a net gain — most
rows previously had no explanation at all — and the wrapper copy stays
localized. Doing it properly means a code→key map generated from the backend's
rule set, which is its own slice.

---

## 96. A capability with a second, divergent path is worse than one with none

**Decided:** 2026-09-05 · `frontend/src/routes/payments/+page.svelte`

This round's premise was that a shipped, tested, documented capability with no
UI is a defect. Two endpoints were nonetheless left unwired on purpose, and the
distinction is worth keeping.

`POST /api/cards/{id}/cancel` exists and works. But every card this app mints
comes from a payment run's `virtual_card` leg and therefore has a `Payment` row,
and `POST /api/payments/{id}/void` — already on the History tab — cancels the
card at the provider *and* reverses the books in one operation. A standalone
Cancel would kill the card while leaving that `Payment` row and its invoice
claiming money is in flight on a now-dead rail: two controls that both close a
card and leave the ledger in different states. `POST /api/cards/generate` books
no `Payment` row at all — it is a card-issuance decision competing with the
payment run, not a rebate control.

So "unreachable" is not the test. The test is whether reaching it leaves the
system in a state the rest of the app can still reason about. The rebate
lifecycle passed that and shipped; these two did not.

The same reasoning shaped the inspections form: `POST /api/inspections` accepts
a body with neither `gr_id` nor `po_id`, but the matcher only ever reads an
inspection through a matched goods receipt or a PO-level row. An unlinked row is
invisible to the very match it was recorded for, so the form requires a receipt
rather than faithfully exposing what the endpoint tolerates.

---

## 97. Rendering "we cannot tell" is a feature, not an omission

**Decided:** 2026-09-05 · `frontend/src/routes/{audit,adaptive}/+page.svelte`

Three surfaces landed this round whose whole value is in *not* stating more than
the data supports, which is the same principle §34 recorded for the residency
alignment verdict and §84 for forecast variance.

**`unsigned` is not a smaller `invalid`.** The approval-signature sweep counts
them separately because a key rollout produces unsigned rows in bulk, and a UI
that merged them into one "problem" figure would report a configuration fact as
suspected tampering. They get separate cards, separate qualifying copy, distinct
badge tones, and only `invalid` is alarm-tinted. An unset signing key renders a
warning-toned notice *before* the counts, not a scary all-unsigned result.

**A threshold recommendation that moved is not an error.** The backend's stale
guard exists for this UI, so the rendered figure is always sent back as
`expected_recommended_threshold` — a guard the client doesn't feed is no guard —
and a 409 gets its own "the recommendation changed, nothing was applied" state
naming both figures, with the current one re-read underneath. "Apply failed"
would misstate both what happened and what to do next.

**Below the minimum sample, the feedback metric renders "not yet measurable".**
`0%` on a two-invoice sample reads as "the automation is never overruled" — the
most reassuring statement available, from the row carrying the least
information.

And the reads that write audit rows are loaded on an explicit act, never polled
or prefetched: a surface that logs the auditor looking at it must not log them
looking at it every thirty seconds.

---

## 98. The auth-coverage gate resolves dependencies by identity, and an allowlist entry must assert something

**Decided:** 2026-09-06 · `backend/tests/test_rbac.py`, `docs/authentication.md`

`test_rbac.py` is the one test that has to fail noisily when someone forgets
RBAC. For a long time it could not. It accepted any dependency whose closure was
*named* `checker` — the house style for all five factories in `deps.py`,
including the two billing entitlement gates. A name is not a security property
in either direction: renaming the closure broke the gate on every route at once,
while an unrelated function called `checker` that authenticated nobody satisfied
it. Alongside it, a single `NO_AUTH_REQUIRED` set asserted *nothing* once an
entry was listed. The two together were demonstrably exploitable — deleting
`Depends(get_scim_tenant)` from all six SCIM `/Groups` handlers, the credential
that creates accounts and grants roles, left the suite green.

The gate now walks `route.dependant` and compares against the actual callables
(`get_current_user`, `get_current_vendor_user`, `get_api_key_principal`);
`require_permission` is identified by its **code object**, one shared `__code__`
across every closure the factory returns. The allowlist splits by obligation, not
category: `PUBLIC_BY_DESIGN` entries are *driven* with a real credential-free
request and must not answer 401/403, so listing a protected route there fails
rather than exempting it; `ALTERNATE_AUTH` entries each name their specific gate.
Three companion tests refuse a stale entry, a route in both lists, and a route
allowlisted despite already having real auth.

Rejected: one list plus a comment convention — a comment is not an assertion,
which is exactly how the SCIM hole survived. **Known limit, accepted:** the
symbol check is static. It catches a gate going away; it cannot catch a gate
called and ignored. The per-route behavioural suites cover that, and the limit is
written down rather than left to be rediscovered.

---

## 99. Tenant engine construction is guarded statically, by discipline rather than by taint

**Decided:** 2026-09-06 · `backend/tests/test_tenant_engine_construction.py`, `.claude/hooks/security-patterns.sh`

`get_tenant`'s JWT org-claim cross-check is only load-bearing if it is reached,
and nothing asserted that it was: a tenant engine built from an interpolated URL
never asks the question. No static rule can prove a `db_name` came from a
resolved `Organization` row rather than from the request, so the guard proves the
weaker, checkable thing — every engine under `app/` is constructed through
`_make_tenant_url(db_name)` or `settings.database_url`, no engine URL is
interpolated, the constructor is never rebound or aliased, and no `feoh_`-prefixed
literal exists outside `config.py`.

Taint analysis was rejected as unmaintainable for the payoff. A blanket ban on
engine construction outside `app/database.py` was rejected because 31 legitimate
sites — background sweeps, dispatchers, webhook handlers — each need a
loop-local engine. The three Lambda handlers are exempt from the *helper* (they
cannot import `app.database` on a dotenv-free path) but not from the rule: the
guard holds them to mirroring its body, and a stale exemption fails too.

The rebound-constructor rule exists because that evasion makes the guard go
*quiet* rather than red — `engine_factory = create_async_engine` removes every
call site from the enumeration. A guard whose failure mode is silence has to
assert against silence.

---

## 100. The audit-coverage guard's unit is the handler, and its route scan has a floor

**Decided:** 2026-09-06 · `backend/tests/test_audit_append_only.py`

Two properties of *how* invariant #3 is enforced are load-bearing and
non-obvious.

**The unit is the handler, not the module.** The sweep required `dispatch_audit`
anywhere in the router module, so one auditing handler vouched for every
unaudited handler beside it — `api/invoices.py` has 21 tenant-mutating routes
behind that single grep, and three unaudited DELETE handlers shipped there and
had to be found by hand in round 23. It now reads `inspect.getsource(endpoint)`
and follows calls to functions defined in the **same module**, so a handler
delegating to a local `_audit(...)` still counts. Exemptions are keyed
`(module, handler)`.

Calls *out of* the handler's module are deliberately not followed. "That other
file audits" is a claim about a chokepoint, and a claim belongs where a reader
can re-check it — not inferred by a source scan that would silently absorb the
day the chokepoint stops auditing. Rejected: a transitive whole-app scan (green
forever once any reachable path mentions `dispatch_audit`), and a runtime
call-every-route harness (needs a live tenant per route, turning the fastest
guard in the suite into the slowest).

**Route discovery asserts a floor.** FastAPI 0.138+ keeps nested routers in
`app.routes`, so the historical `isinstance(r, APIRoute)` filter saw 1 route out
of 564 and every filter built on it yielded `[]`. The swap to
`iter_route_contexts` fixes today; `_MIN_EXPECTED_ROUTES` is what fixes tomorrow.

Six handlers the re-keying exposed sit in `_OPEN_AUDIT_HOLES`, apart from the
real exemption dict and labelled as holes rather than decisions — widening the
exemption dict to reach green would have made a known gap indistinguishable from
a settled call.

---

## 101. The money-`Numeric` guard is opt-out, not opt-in

**Decided:** 2026-09-06 · `backend/tests/test_money_invariants.py`, `backend/docs/database.md`

The sweep sought `Float` only on columns whose *name* matched a token list
(`amount`, `total`, `price`, `subtotal`, `_tax`, `amount_`). That made the money
invariant opt-in: a column was protected only once someone spelled it a way the
tokens happened to cover. The list had drifted — 35 of 88 `Numeric` columns
matched no token, including every FX rate at `NUMERIC(18,8)`, all four
expense-policy thresholds, `contracts.spend_limit`, and `invoice_line_items.tax`
(the token carried an underscore) — so a new `Column(Float)` named `balance`,
`fee` or `*_rate` passed the whole file.

Inverted: **every** `Numeric` must declare precision and scale, and **no** binary
float may exist outside `NON_MONEY_FLOAT_ALLOWLIST` with a written reason. Adding
a money column costs nothing; adding a float costs an argument in writing. The
allowlist shipped **empty** — the schema has zero float columns — and a stale
entry fails its own test, since a leftover key would pre-approve a future column
of that name.

Rejected: a second, broader name-token sweep for a money column typed `String`.
Measured, a token list wide enough to be useful (`balance`, `fee`, `cost`,
`_rate`) flags 9 legitimate non-money columns, and an allowlist for *those* is
the same hand-maintained list this change deleted, one layer down. The `String`
hole is closed instead by an exact-name assertion over nine unambiguous money
names — zero false positives, so no allowlist — documented as a supplement, not
a completeness claim.

Also rejected: walking `Base.registry.mappers` after `import app.models`.
`usage.py` is not re-exported from `__init__.py`, so that walk silently skips it.
The sweep imports every module in the package and reads `Base.metadata.tables`,
with a non-vacuity floor — a guard that iterates nothing passes everything.

---

## 102. The SoD endpoint pin is derived from the app, not hand-listed

**Decided:** 2026-09-06 · `backend/tests/test_sod_endpoint_wiring.py`

The pin hand-listed the routes that must gate on `require_permission`. A
hand-list omits, and what it omitted was the worst possible subset: the eight
routes that actually move money (`runs/{id}/execute`, `{id}/void`,
`settlement/accept`, `compliance/release`, `compliance/dismiss`, `resume`,
`sync-erp`, `retry-failed`) plus the whole `/api/admin` `user.manage` surface —
leaving two of the eight catalogue permissions never imported by the guard at
all. Everything was correctly gated; nothing proved it stayed that way, on the
one class of route where the granular-permission layer existing at all is the
point.

The pin now walks the live app's `route.dependant` and asserts `CASES` ≡ the set
of `require_permission` routes, in both directions, and that every
`ALL_PERMISSIONS` entry gates at least one pinned route so a catalogue addition
wired to nothing is caught as dead config. It stores the **exact** any-of set
rather than "must include this permission", because a widening — adding
`payment.void` to the `/execute` gate — is as much an SoD failure as a narrowing.

Two deliberate exclusions, commented so they read as decisions: `runs/{id}/approve`
(CFO sign-off) stays on `require_roles` so it cannot be granted to whoever holds
the broader create-draft permission, and `/admin/roles` CRUD stays role-gated
because role CRUD *defines* permissions — gating it on one would let a custom
role mint itself everything.

---

## 103. The dashboard's top-vendor tile groups in SQL, and its ties are broken by name

**Decided:** 2026-09-06 · `backend/app/api/dashboard.py`, `backend/docs/analytics.md`

The tile selected five columns of every non-rejected invoice — all time, no
`LIMIT` — and folded them in Python, while the rollup, pipeline, aging and trend
blocks either side of it all `GROUP BY` in SQL and say so in their comments. Two
problems: the fold was a synchronous per-row loop inside an `async def`, the
shape the "blocking work does not run on the event loop" invariant forbids, on
the landing page; and it grew linearly with the invoice table while its four
neighbours stayed one aggregate each. Measured at 190 000 invoices: 505.7 ms, of
which 270.3 ms was the fold, against 28.5 ms for the equivalent `GROUP BY` —
17.7×.

Rejected: keeping the fold and offloading it with `asyncio.to_thread`, which
unblocks the loop but leaves the unbounded transfer and the linear growth; and
pre-aggregating into a stored per-vendor total, the compute-on-read discipline
the rest of analytics deliberately avoids. The rewrite reuses the `_rep_expr`
CASE already in scope rather than restating the conversion rule, so the tile
cannot drift from the figure directly above it — the same one-owner argument as
§82's budget rollup.

It also **tightened** the ordering. The fold sorted on converted total alone and
Python's sort is stable, so equal-spend vendors came back in DB scan order: which
took rank 10, and which fell off the `[:10]`, could differ between two identical
requests. The SQL orders `(total DESC, vendor ASC)`. A non-reproducible top-10 is
not a defensible answer to give a CFO twice.

An index was considered and refused: `EXPLAIN ANALYZE` shows a parallel seq scan
removing only 10% of rows, so a covering index would be near heap-sized and add
write cost on the tenant's hottest table for no plan change.

---

## 104. An index declared only in a migration reaches half the fleet

**Decided:** 2026-09-06 · `backend/alembic/versions/0092_list_and_audit_indexes.py`, `backend/app/models/`

Tenants are provisioned two ways: `alembic upgrade head` for existing ones, and
`Base.metadata.create_all` in `tenant_provisioning._create_tenant_tables` for new
ones. `create_all` builds exactly what the ORM declares, so an index written only
into a migration silently never reaches a freshly-provisioned tenant.

Migration `0010_audit_log_shipping` added `ix_audit_log_shipped_at_null` for the
shipper's `shipped_at IS NULL ORDER BY created_at` sweep and never declared it on
`AuditLog`. For 82 revisions a migrated tenant had it and a provisioned one ran
that sweep — per tenant, every 60 seconds — as a full sequential scan of its
largest table: 39.740 ms / 30 003 buffers at 1.2 M rows, to return nothing.

The rule: **every index is declared in both places, under one name.** The
migration is for tenants that already exist; the model `__table_args__` is for
the ones that don't yet. `test_list_and_audit_indexes.py` compares the two
against `pg_get_indexdef` on a real provisioned tenant, so a name, column order
or partial predicate that disagrees fails.

Rejected: a second index under a new name — it would have worked, and cost every
migrated tenant a duplicate partial-index write on every audit row forever.
Adopting the existing name means this revision must not drop it on downgrade: an
index a revision *ensures exists* is not one it *owns*, and the two lists are
kept apart for that reason.

Also settled: **no `CREATE INDEX CONCURRENTLY` in Alembic revisions.** It cannot
run inside the migration transaction, and paired with `IF NOT EXISTS` a cancelled
build leaves an INVALID index that every later run skips while reporting success.
Measured build cost is ~2.5 s for a 17-index revision at 1.2 M audit rows; an
operator needing less lock pre-builds by hand and verifies `indisvalid`, after
which the migration no-ops.

---

## 105. The SOX audit export streams, and is never capped

**Decided:** 2026-09-06 · `backend/app/api/audit.py`, `backend/docs/audit-log-shipping.md`

`GET /api/audit/export` materialised every row in the range as ORM objects and
built the whole body in memory; an annual range is tens of thousands of rows.

Rejected: a `LIMIT`, which makes the memory problem trivial and the endpoint
worthless — a silently short export is evidence an auditor signs off on, strictly
worse than a slow one. Rejected: deferring the `audit.exported` row until the
stream completes, which would lose the record of an aborted download even though
the access happened.

Chosen: a `yield_per` cursor over plain columns plus a chunked
`StreamingResponse`, with the range pinned to a snapshot from the *database*
clock — comparing against the process clock would drop or admit rows on skew —
so the export cannot contain the record of itself, and `COUNT(*)` +
`DISTINCT actor_id` folded into one aggregate so the range is scanned once. Peak
server allocation went from linear in the range (70.6 MiB at 20 000 rows) to flat
1.5 MiB, output byte-identical.

Two consequences accepted deliberately: the request holds its tenant connection
for the whole response, and the mechanism depends on FastAPI keeping
`yield`-dependency teardown after the body drains — internal ordering, guarded by
a raw-ASGI test against a real database rather than assumed. CSV traded ~20% wall
clock, which measurement attributed to the *database* still sorting the range;
`ix_audit_log_created_at` (§104's revision) removes that sort node.

---

## 106. A detail modal is a list of one, and it is the request-identity case that bites hardest

**Decided:** 2026-09-06 · `frontend/src/routes/{vendors/screening,audit,experiments,admin/api-keys}`

The app-wide `createRequestSequencer` sweep landed on lists, so fetches that
*look* like a single read — a modal opened from a row, a drill-down opened from a
finding — were left unguarded. They are not single reads: "open A, close it, open
B" re-issues the same request with a different subject, and the failure is
invisible by construction, because the heading renders from the click and the
body from the response.

Rejected: comparing the response to the current selection inside each handler —
the same guard written four more times, and it gets the loading/error bookkeeping
wrong (`canCommit` vs `isCurrentRequest`). The rule is the one lists follow — one
sequencer per independent request stream — plus a cheap second obligation: the
panel exposes **both** ids in the DOM, the subject it was opened for and the id
the response claims, so a mismatch is assertable rather than inferred from
rendered content.

`/vendors/screening` settled the harder half. A modal that *acts* has a third
identity to bind beyond the heading and the body: the target of its buttons.
Block/Unblock read the live `selected` after its awaits and `rescreen` re-read it
after two, so a reviewer could read one vendor's sanctions timeline while the
control blocked another. The rule: an action captures its subject at click time,
and everything after the first await reads that capture, never the live
selection; its `busy` flag is keyed to that subject for the same reason.

Its history modal also gained a real error state ordered above the empty one —
"we could not look" must outrank "there is nothing", directly above a
Block/Unblock control whose decision rests on that timeline.

---

## 107. A payment states its currency, or states that it cannot

**Decided:** 2026-09-06 · `backend/app/schemas/payment.py`, `backend/app/api/payments.py`, `frontend/src/routes/payments/+page.svelte`

`payments` has no currency column and `payment_runs.total_amount` is one bare
`Numeric`. Both are legitimate — a payment settles in its invoice's currency, and
run creation 422s a run spanning more than one — but neither response carried the
code, though both already joined the invoice row for `vendor_name`. Every
`/payments` reader therefore rendered per-row money under the org's default.

The sharpest instance was the Accept-settlement dialog. It exists because a rail
can settle a different amount, or a different currency, than AP authorized, and
it puts "Authorized" beside "Settled". `settled_currency` was on the wire; the
authorized figure's currency was not. A EUR payment rendered a fabricated
`$1,200.00` directly above a real `€1,150.00` — on the screen built to catch
`currency_mismatch`.

Both responses now carry it, and where it cannot be **proven** the answer is
`None`, never a substituted default (§79/§82). The deciding case is a legacy run
predating the single-currency guard: its `total_amount` is a sum across
currencies, denominated in nothing real, so naming either leg's code would dress
a meaningless figure up as a genuine one. `_one_currency` is one shared rule
applied by the list, the detail and the create response, so the three cannot
publish different codes for the same run. The client renders an unprovable code
as a bare grouped figure.

Rejected: defaulting to the org's reporting currency (the fabrication being
removed), and converting on read (a rate fetched on a read makes a historical
figure move under the reader — §18).

**The guard is the assertion, not the field.** The e2e suite matched digits only,
so `$500.00` and `€500.00` were indistinguishable to it and this shipped unseen.
The payments specs now assert the currency **symbol**, and the settlement fixture
is EUR rather than the tenant's own currency — a same-currency fixture makes the
wrong rendering look right.

---

## 108. OFFSET paging needs a total order, and the guard for it is a source guard

**Decided:** 2026-09-06 · `backend/app/api/`, `backend/tests/test_pagination_total_order.py`

`OFFSET`/`LIMIT` paging is only coherent if the `ORDER BY` is a total order.
`created_at` is not one: it defaults to the transaction timestamp, so every row
written by a single transaction — an ERP sync page, a CSV import, one sweep tick
— shares it *exactly*. Postgres is then free to order the tied rows differently
between the two queries that fetch page 1 and page 2, so a row can be handed to
the caller twice or skipped altogether.

Two endpoints were reported (`api/exceptions.py`, `api/purchase_orders.py`,
found while indexing their tables in §104). Sweeping for the shape found **26**,
including four supplier-portal lists, the notification centre, credit memos,
cards and the SCIM user list. `exceptions.py` was the clearest case: its own
`/ids` select-all resolver three lines below the list already tie-broke on `id`,
so the list disagreed with the resolver it exists to feed. All 26 now append the
primary key.

**The guard is a source guard, deliberately, and that decision was forced by a
failed attempt at the obvious one.** A runtime test — write N rows in one
transaction so their timestamps tie, page through them, assert each appears once
— was written first and **passed without the fix**. At fixture scale Postgres
returns a stable scan order, so the test proved nothing and would have shipped as
a guard that never fails: precisely the vacuity §100 and §101 were about, and it
was discarded rather than tuned until it happened to go red. The manifestation is
planner-dependent; the *property* the correctness argument rests on is not. So
the guard asserts the property — an AST scan for `.offset(...)` chained to an
`.order_by(...)` whose keys are all timestamp-ish — and says in its own docstring
why it is not a runtime test.

It keys on `.offset(...)` rather than on `order_by` alone because a bare
`ORDER BY created_at` is perfectly correct on a `LIMIT 1` lookup, an aggregate,
or a whole-set fetch — nothing splits tied rows across two requests there. The
unnarrowed version flagged 62 sites, most of them fine; a guard that cries wolf
gets its exemption list padded until it means nothing.

---

## 109. A migration-only index is invisible to half the fleet, so parity is a test, not a habit

**Decided:** 2026-09-06 · `backend/alembic/versions/0093_migration_only_indexes.py`, `backend/tests/test_migration_model_index_parity.py`

§104 fixed one instance of this; an audit of all 216 `CREATE INDEX` statements
found **twenty**. A database here is built two ways and only one runs Alembic, so
an index written into a migration and never declared on its model reaches
migrated databases and silently never reaches a freshly-provisioned one. Nothing
notices — the reads still return correct rows, just by sequential scan — and
where the index is UNIQUE, the invariant it enforces is simply absent on half the
fleet. Verified against a tenant with no `alembic_version` row at all, and
against one stamped at head that was nonetheless missing every one: **"at head"
is not evidence the indexes exist.**

**Two were correctness, not performance.** `uq_positive_pay_run_format` is the
*only* concurrency control under the check-issue endpoint's read-then-insert —
without it the handler's `except IntegrityError` branch is dead code, two
concurrent calls both insert, and its own `scalar_one_or_none()` idempotency
lookup then raises `MultipleResultsFound` on **every later call**: a permanent
500 on that run, plus a second MinIO object carrying full account and routing
numbers. `uq_subscription_one_live_per_org` is the one-live-subscription-per-org
billing invariant that `uq_subscription_org_plan` does not bound, since two rows
for two different plans satisfy it.

**Two were not defects and are resolved the other way**, because declaring them
would build a genuine duplicate: `ix_bank_transactions_matched_payment` is the
exact column and predicate of the UNIQUE index 0081 added (which never dropped
it, so migrated tenants carried pure write overhead on a hot table), so 0093
drops it; `ix_vendor_change_requests_org_id` is the model's
`ix_vendor_change_requests_organization_id` under another name, so 0093 converges
on the model's spelling, creating before dropping so the column is never briefly
unindexed. Rejected: declaring both spellings, and `ALTER INDEX … RENAME`, which
is correct only on the one database shape that has the source and not the target.

0093 **ensures, it does not own** — restating an earlier revision's CREATE is a
catch-up for an already-provisioned database, so its downgrade drops none of the
eighteen (§104's `_ADOPTED` semantics). Where a UNIQUE index is involved it
**pre-flights and refuses** with a counts-only, PII-free message rather than
failing mid-revision: choosing which of two Positive Pay files went to the bank
is an operator's judgement, not a silent `DELETE` in a migration — the call §72
made for duplicate GL codes.

The durable half is the guard, not the migration. New tenants keep being
provisioned by `create_all`, so a migration alone would leave the *next* tenant
in the state being repaired. The parity test is opt-out — every
`CREATE [UNIQUE] INDEX` in every revision, checked against the models, with
exemptions needing a written reason that is itself re-checked so it cannot rot.
