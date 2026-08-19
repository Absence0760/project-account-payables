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
