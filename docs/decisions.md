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
