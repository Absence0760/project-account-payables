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

**Last reconciled:** 2026-08-15 against `feat/webhook-secret-rotation`. Closed in
that pass: **outbound-webhook signing secrets have no rotation path** — both
halves shipped together (`POST /api/webhooks/{id}/rotate-secret` keeping the
subscription id + delivery history, and the overlap window via migration `0084`
plus the `X-Webhook-Signature-Previous` header), so the deferral's "shipping only
the no-overlap half leaves the hard cutover a customer will actually hit" no
longer applies.

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

### Outbound-webhook secret rotation is API-only — no admin UI

`POST /api/webhooks/{id}/rotate-secret` ships with its overlap window, docs and
tests, but `frontend/src/routes/admin/webhooks/+page.svelte` has no control for
it — the page can create, edit and delete a subscription, so an admin who needs
to rotate a leaked secret has to reach for the API while a Delete button (which
CASCADE-deletes the delivery history) sits right there in the UI. That's the
wrong affordance to be the easy one during an incident.

- [ ] Add a rotate action to the subscription row: confirm-then-act like the
      existing armed two-click Delete, an overlap picker (default 60 min, plus
      an explicit "compromised — cut over now" = 0 option), and a
      show-once secret panel reusing whatever the create flow already does for
      the one-time secret reveal.
- [ ] Surface an "overlap active until …" badge while
      `previous_secret_expires_at` is in the future, so an admin can see a
      rotation is mid-flight rather than guessing.

**Why deferred:** the backend round that closed the rotation follow-up was
scoped to the endpoint + overlap + docs (what that item actually listed); the
UI is a different surface with its own patterns (`Modal`, `RowAction`, the
one-time-secret reveal) and deserves its own pass rather than being bolted on.
**Trigger:** the first tenant expected to self-serve a rotation without an
engineer, or the next `/polish-ui` pass touching `/admin`.
Ref: [public-api.md](../backend/docs/public-api.md) § Rotating a signing secret.

### Vendor statement reconciliation — PDF intake

CSV upload and the manual pasted-lines path both ship. A supplier statement that
arrives as a PDF still has to be transcribed by hand.

- [ ] PDF-via-extraction statement intake + raw-file storage — route the PDF
      through the existing extraction pipeline rather than adding a second
      parser.

**Why deferred:** the CSV/manual paths cover the common case, and reusing the
extraction adapters properly is a real slice rather than a bolt-on.
**Trigger:** a pilot tenant whose suppliers send PDF statements.
Ref: [vendor-statement-reconciliation.md](../backend/docs/vendor-statement-reconciliation.md) § Deferred.

### Mount-time double-fetch race — invoices/vendors' local-mutation bypass

`frontend/src/lib/components/admin/UsersPanel.svelte` had two `$effect`s that
both called `adminStore.fetchUsers()` on mount — the search-debounce effect
fired an unguarded duplicate ~250ms after the immediate one (a Svelte
`$effect` always runs once on mount regardless of whether its tracked value
changed). Because the store always *replaces* the list wholesale, whichever
of the two fetches resolved last could silently clobber an optimistic
create/delete with a stale snapshot — a real, user-visible race, not a test
flake (root-caused and fixed via `/flake-doctor`, PR #286).

An independently-verified `/bug-hunt` sibling sweep (each page's actual
`$effect` blocks read, not just grepped) found the identical pattern —
unguarded duplicate mount fetch + a local-only mutation splice with no
sequencer — on five more pages, now fixed the same way (a guard skipping
the second effect's own mount-time run): **`budgets`, `contracts`, `intake`,
`recurring`, and `purchase-orders`** (the last caught in code review, missed
by the initial sweep — narrower blast radius since it's read-only/ERP-synced
with no local-splice mutation, but `syncFromErp()`/`loadMore()` could still
be overwritten by the delayed duplicate). Five other pages were checked and
confirmed NOT at risk: `catalogs` and `expenses` only ever have one
fetch-triggering effect; `payments`, `positive-pay`, `requisitions`,
`vendor-statements` each have a request sequencer and/or route every
mutation through a full sequencer-protected refetch, closing the race from
a different angle.

**Still open — a narrower variant on `invoices` and `vendors`:** both pages
already carry a request sequencer (`createRequestSequencer()`) that correctly
resolves fetch-vs-fetch ordering, so the `UsersPanel`-style guard doesn't
apply to them. But each has **local-mutation helpers that bypass the
sequencer entirely**: `invoiceStore.update()` / `patchLocal()` (used by
`InvoiceModal`'s save/approve/reject/file-attach) and `vendors/+page.svelte`'s
`applyVendorUpdate()` (bank-detail edit, screening, risk-recompute,
block/unblock) mutate the list directly without calling
`fetchSequence.start()`. A still-in-flight mount-time fetch — the sequencer
only drops it if a *newer sequenced fetch* supersedes it, which these local
mutations never trigger — can resolve after one of these edits and overwrite
it with a stale pre-edit snapshot.

- [ ] Route `invoiceStore.update()`/`patchLocal()` and
      `vendors/+page.svelte`'s `applyVendorUpdate()` through the same
      sequencer their pages already use (mark the local mutation as
      superseding any in-flight fetch, or have it call
      `fetchSequence.start()`/mark-latest before applying), so a stale
      redundant fetch can never clobber a local edit either.

**Why deferred:** this is a different code shape from the four just fixed
(threading state through an existing sequencer rather than adding a mount
guard) and touches two higher-traffic pages — worth its own focused pass
rather than folding into the mechanical sibling-sweep fix.
**Trigger:** next `/flake-doctor` or `/bug-hunt` pass touching `invoices` or
`vendors`, or a bug report matching this symptom on either page.
Ref: `reviews/flake-admin-users.md` (gitignored — regenerate via
`/flake-doctor` if consulting this again after the file has aged out).

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
