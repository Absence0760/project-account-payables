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

**Last reconciled:** 2026-08-15 against `improve/round-followup-batch-2` — a
three-agent round that closed the two remaining actionable `(c)` items and
shipped one backend improvement chosen by survey.

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
[roadmap_shipped.md](roadmap_shipped.md). It surfaced two new items below — the
keyless-dev-box extraction fallback, and the reader's uncounted skipped rows.

**The invoices/vendors local-mutation race closed** — the fix went into the
shared `createRequestSequencer()` primitive rather than being hand-rolled per
page, splitting the old single `isLatest` predicate into `canCommit` (may this
response be written?) and `isCurrentRequest` (is this still the newest request?)
so a `finally` clearing a loading flag doesn't hang forever once a local edit
supersedes an in-flight fetch. Rationale in [decisions.md](decisions.md) §23.
The exhaustive sweep it prompted found the far larger remainder — eighteen list
surfaces with no sequencing at all — and a separate `/assistant` defect, both
new entries below.

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
failure count — is a new entry below.

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

### Every background sweep throws away its own failure count

All fourteen long-lived sweeps started in `backend/app/main.py`'s lifespan share
one shape:

```python
while True:
    try:
        await <sweep>_once()          # <-- return value DISCARDED
    except Exception as exc:
        logger.error("[x] sweep raised: %s", exc.__class__.__name__)
    await asyncio.sleep(interval)
```

Twelve of them already return a result dataclass carrying a `failures: int`
(`extraction_reaper`, `audit_log_shipper`, `approval_escalation`,
`payment_reconciler`, `contract_renewal`, `vendor_rescreen`,
`discount_auto_trigger`, `qms_sync`, `recurring_invoices`, `retention_sweep`,
`scheduled_reports`, `cash_flow_alerts`) — and **every loop discards it at the
call site**. The counter's only consumer is a conditional aggregate
`logger.info` inside `*_once` itself. It is never persisted, never exposed, never
alerted on. There is also no supervision (`asyncio.Task` with no
`add_done_callback`, no restart) and `GET /api/health` returns a static `ok`
that says nothing about whether any sweep is alive or progressing.

Consequence: a sink that has been misconfigured for months (the `audit_shipping`
adapters raise by design so rows stay unshipped and retry forever — the SOC 2
WORM evidence trail simply isn't leaving the tenant DB) looks identical to one
running clean.

- [ ] Read the `failures` count in each loop and turn it into queryable state —
      the two sweeps that already model failure as state are the pattern:
      `scheduled_reports` persists `last_run_status` / `last_run_error` and
      auto-disables after 5 consecutive failures, and `webhook_deliveries` keeps
      per-row status + attempt count. A per-sweep run row (or a settings-JSON
      marker, no migration needed) plus a non-zero-streak signal would convert
      ten of these from invisible to detectable.

**Why deferred:** surfaced by the survey behind this round's `payment_erp_sync`
and `vendor_rescreen` fixes, which closed the two *specific* cases where an
invisible failure also lost work or blocked progress. This entry is the
remaining *systemic* observability gap — it touches fourteen files and wants one
consistent mechanism decided first, not fourteen ad-hoc ones.
**Trigger:** the next operability/observability pass, or the first deployed
environment where a sweep is suspected of not running.
Ref: `backend/CLAUDE.md` § Key background services.

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

### The axe a11y guard doesn't cover `/admin` or `/vendor-statements`

`frontend/tests-e2e/a11y/axe.spec.ts` covers dashboard / invoices / vendors /
payments / exceptions / login / portal. **No `/admin` route is in it** — not
`/admin` itself, not `/admin/api-keys`, `/admin/webhooks`, or `/admin/partner`.
Those pages carry dialogs, armed two-click destructive actions and one-time
secret reveals, which is exactly the surface where a focus-management or
labelling regression is most costly, and the guard would not catch it.

`/vendor-statements` is missing too, and its create modal has since gained a
radio `fieldset`/`legend` intake picker, a file input and a persistent
`role="alert"` refusal region — new interactive controls with no axe pass.

- [ ] Add the `/admin` routes and `/vendor-statements` to the axe spec's route
      list (they reuse the shared `ui/` primitives, so the expectation is that
      they pass as-is; if one doesn't, that IS the finding).

**Why deferred:** surfaced while adding the webhook rotation UI, and again while
adding the statement upload UI. Widening a shared guard spec at the end of an
unrelated round is the wrong moment — a new failure there would be
indistinguishable from a regression the round caused. (Both rounds ran in
parallel worktrees, where a shared spec is also the file most likely to
conflict.)
**Trigger:** the next `/audit:accessibility` or `/polish-ui` pass touching
`/admin` or `/vendor-statements`.
Ref: [accessibility.md](accessibility.md).

### Every other list surface is still unsequenced — a local edit races its fetch

`frontend/src/lib/utils/requestSequence.ts` grew `supersedeInFlight()` so a
row edited in place can't be reverted by a fetch that was already in flight,
and `/invoices` + `/vendors` were wired to it. A sibling sweep afterwards —
every `+page.svelte` and list store **read**, not grepped — found that **no
other list surface has sequencing of any kind**: not `createRequestSequencer`,
not a hand-rolled token counter, not an `AbortController`. Eighteen of them
both replace the list wholesale from a fetch *and* edit a row locally with no
fetch, which is exactly the clobber just fixed.

Racing: the `contracts`, `expenses`, `notifications`, `admin` (users + roles)
and `workflows` stores; the `discounts`, `positive-pay`, `recurring`,
`budgets`, `intake`, `requisitions`, `catalogs` (delete only),
`vendor-statements`, `vendors/screening` (its Refresh button is the second
trigger) and `workflows/[id]` routes; the policies and pre-approvals
sub-lists on `expenses`; and `InvoiceModal`'s line-item list.
`VendorConsolidationModal` was checked and is the one provably safe surface —
mount-only fetch, no create path.

Two details worth keeping, because they defeat the obvious dismissals:

- **A create/prepend path needs no existing row.** "The mount fetch must have
  landed before there's a row to mutate" closes the race for edit/delete but
  not for New/Add, which is live while the first GET is still out.
- **The `untrack()` fix from issue #168 never reached four of these pages.**
  On `contracts`, `recurring`, `budgets` and `intake` the filter `$effect`
  calls `buildParams()`, which reads `search` directly — Svelte tracks that
  transitively, so every keystroke fires an immediate undebounced fetch
  *alongside* the 300 ms debounced one: two concurrent loads, either able to
  clobber. Those pages' `searchEffectRan` guards cover the duplicate **mount**
  fetch only and do nothing here.

- [ ] Adopt `createRequestSequencer` on each surface above (`start` →
      `canCommit` → `isCurrentRequest`, plus `supersedeInFlight()` in the
      local-mutation helper). The primitive and the pattern doc
      (`frontend/CLAUDE.md` § Sequencing list fetches) now exist for this.
- [ ] Apply `untrack(() => search)` to the four filter effects above.

**Why deferred:** the round that built the primitive ran in a worktree fenced
to `/invoices` + `/vendors` while two other agents held `/cfo`,
`/vendor-statements` and the a11y specs — several surfaces above sit in their
files, so an eighteen-file sweep from here would have collided on merge.
Nothing about the fix is unknown; it is mechanical per surface.
**Trigger:** the next frontend round that has the whole app to itself, or any
page above being edited for another reason — fix it there and then, rather
than recopying the pattern.
Ref: [decisions.md](decisions.md) §23.

### `/assistant` loses a message — and overwrites another — if you send while a thread loads

`openConversation` (`frontend/src/routes/assistant/+page.svelte:65`) opens
with `if (busy) return` but never sets `busy = true`; only `send()` does. The
composer therefore stays live while the conversation GET is in flight. Send
in that window and `send()` pushes the user and placeholder-assistant bubbles
and captures `assistantIdx` against the current array; the GET then resolves
and replaces `messages` wholesale, dropping both; `applyFinal` (:90) writes
the model's answer into `messages[assistantIdx]` of the **new** array. The
answer doesn't just go missing — it lands on top of an unrelated historical
message.

- [ ] Hold `busy` for the duration of `openConversation` (its own dead guard
      shows that was the intent), and resolve the placeholder by identity
      rather than by captured index, so a replaced array can't misdirect the
      write.

**Why deferred:** found by the sibling sweep above, not by the round's own
change, and `/assistant` was outside that round's worktree fence. It is a
display-integrity bug on a read-only surface — it moves no money and writes
nothing server-side — so it did not warrant breaking the fence.
**Trigger:** the next `/assistant` change, or the sequencer sweep above.

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
