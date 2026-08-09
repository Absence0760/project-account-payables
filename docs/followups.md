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

**Last reconciled:** 2026-08-06 against `701a0c1c`. Two docs-drift items from
the first pass (stale Expense Management and Supplier Portal statuses) were
**closed** by the roadmap split rather than carried — their durable fix was the
split itself.

---

## (c) Feature work — sized and unstarted

### AI Cash-Flow Copilot — Phase 3 (draft-only enactment)

The only explicitly-planned unshipped feature. Phases 1–2 shipped (read-only cash
Q&A, `propose_payment_plan`, the display-only `PlanCard`); the plan card has no
enact affordance.

- [ ] `POST /api/cash-flow/plans/{id}/draft-run` — idempotent draft payment-run
      creation. Execute stays CFO-gated; the copilot's boundary
      ([decisions §18](decisions.md)) holds — a draft run moves nothing.
- [ ] `POST /api/cash-flow/plans/{id}/capture-discounts` — status-only accept.
- [ ] Human-confirmed + audited on both paths; enact affordance on
      `PlanCard.svelte`.

Deferred within the same track, listed so they aren't lost:

- [ ] Saved plans / plan-vs-actual (`CashPlan` model + migration)
- [ ] Opening-balance provenance surfacing
- [ ] Consolidated cross-entity mode
- [ ] Proactive shortfall-alert sweep

**Trigger:** next feature slice. Nothing blocks it.
Refs: [roadmap.md](roadmap.md) § AI Cash-Flow Copilot,
[cash-flow-copilot.md](cash-flow-copilot.md).

### i18n — the date-localization slice

16 `.svelte` files still call `toLocaleDateString` inline instead of the
locale-aware `frontend/src/lib/utils/time.ts::formatDate`, so those dates ignore
the active locale. Last open item on the i18n track; every string catalogue and
every route/modal extraction has shipped.

- [ ] Routes — `purchase-orders`, `goods-receipts`, `requisitions`, `contracts`,
      `tax`, `positive-pay`, `recurring`, `organization`, `profile`, `billing`
- [ ] Components — `modals/InvoiceModal`, `modals/VendorModal`,
      `modals/PositivePayModal`, `chat/SupplierChatThread`,
      `exceptions/AgentDashboard`, `assistant/ToolResultView`

`utils/time.ts` and `utils/time.test.ts` are the implementation and its test —
they legitimately call `toLocaleDateString` and are out of scope.

**Why deferred:** batched deliberately rather than dribbled across unrelated PRs,
since it's one mechanical sweep with one guard.
**Trigger:** any PR that touches date rendering, or a standalone sweep.
**Durable fix:** route all of them through `formatDate` and add a source-scan
guard so a new inline `toLocaleDateString` in `routes/`/`components/` fails the
suite — otherwise the class reopens.
Ref: [roadmap.md](roadmap.md) § Internationalization.

### Billing — live-Stripe plan-change UI

Backend `POST /api/billing/change-plan` is shipped (Decimal-exact proration,
idempotent, audited). The frontend button at
`frontend/src/routes/billing/+page.svelte:302` is deliberately disabled with a
"coming soon" title so the page reads complete without implying an affordance
that isn't wired.

- [ ] Wire the plan-change flow; replace the disabled affordance.

**Blocked on:** rides the live-Stripe path — needs a provisioned Stripe account
to verify against (see the credentials section below). Testable against the
`mock` adapter first.
Ref: [billing.md](../backend/docs/billing.md) § Customer-facing UI.

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

### In-source TODOs

- [ ] `backend/app/api/vendors.py:1014` — add `list_vendors()` to the
      `ErpAdapter` interface. Currently the sync path works around its absence.
- [ ] `mobile/lib/services/push_service.dart:49` — send the device token to the
      backend for targeted push. **Blocked with the FCM/APNs credentials below**;
      the local half is what's sized here.
- [ ] `mobile/lib/services/push_service.dart:99` — deep-link a notification tap
      to the specific invoice (`message.data['invoice_id']`).

---

## (c) Diagnosed defects awaiting a fix

Full write-ups — root cause, evidence, blast radius, recommended fix — live in
[known-issues.md](known-issues.md). Listed here only so the ledger is complete.

- [x] ~~**Read-after-write race on every mutating endpoint.**~~ **Fixed
      2026-08-06** — `commit_before_response` moves the success-path commit onto
      the exit stack FastAPI unwinds before sending, so a `201` is no longer
      returned for an uncommitted write. See [decisions.md §20](decisions.md);
      regression coverage in `backend/tests/test_commit_before_response.py`.
      *(Pruned from this list on the next pass.)*
- [x] ~~**Workflow-mutating e2e specs can strand a tenant on a disabled
      workflow definition.**~~ **Resolved 2026-08-08** — audit found every
      mutating spec already restores state via `try/finally` or a throwaway
      definition; added the missing piece, a `globalSetup` guard
      (`frontend/tests-e2e/fixtures/globalSetup.ts`) that asserts every
      tenant's default workflow shape before any test runs. See
      [known-issues.md](known-issues.md). *(Pruned from this list on the next
      pass.)*
- [ ] A dev backend on the same Postgres mutates the pytest tenant DBs mid-test.

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
- [ ] **Stripe Billing** — a provisioned Stripe account for the live
      `stripe_billing` adapter path (also unblocks the plan-change UI above).
      Ref: [billing.md](../backend/docs/billing.md).
- [ ] **Mobile push (FCM + APNs)** — a Firebase project,
      `google-services.json` / `GoogleService-Info.plist`, and an APNs auth key.
      Unblocks both `push_service.dart` TODOs.
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

---

